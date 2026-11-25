"""Fluxos do portal. Cada registro pertence a uma única malharia."""
import hashlib
import re
import secrets
from datetime import datetime, timedelta

from flask import abort, flash, redirect, render_template, request, session, url_for
from markupsafe import escape
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

SECOES = {
    "suporte": {"chamado"},
    "sugestoes": {"sugestao"},
}
CATEGORIAS_SUGESTAO = ("Facilidade de uso", "Funcionalidades", "Outro")
EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")


def instalar(app, db, Empresa, empresa_publicavel, identificar, enviar_email, limitar, filtros_busca):
    class Registro(db.Model):
        __tablename__ = "portal_registro"
        id = db.Column(db.Integer, primary_key=True)
        empresa_id = db.Column(db.ForeignKey("empresa.id", ondelete="CASCADE"), nullable=False, index=True)
        tipo = db.Column(db.String(24), nullable=False)
        dados = db.Column(db.JSON, nullable=False)
        criado_em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    class Membro(db.Model):
        __tablename__ = "portal_membro"
        id = db.Column(db.Integer, primary_key=True)
        empresa_id = db.Column(db.ForeignKey("empresa.id", ondelete="CASCADE"), nullable=False, index=True)
        email = db.Column(db.String(254), unique=True, nullable=False)
        nome = db.Column(db.String(100), nullable=False)
        senha = db.Column(db.String(255))
        convite = db.Column(db.String(64), unique=True)
        validade = db.Column(db.DateTime)

    def csrf():
        if "portal_csrf" not in session:
            session["portal_csrf"] = secrets.token_urlsafe(32)
        return session["portal_csrf"]

    app.jinja_env.globals["csrf_visitante"] = csrf

    @app.template_global()
    def nome_usuario_portal():
        if session.get("portal_membro"):
            membro = db.session.get(Membro, session["portal_membro"])
            return membro.nome if membro else ""
        empresa = db.session.get(Empresa, session["empresa_id"]) if session.get("empresa_id") else None
        if empresa:
            return " ".join(filter(None, [empresa.responsavel_nome, empresa.responsavel_sobrenome])) or empresa.email
        return ""

    def conferir_csrf():
        if not secrets.compare_digest(csrf().encode(), request.form.get("csrf_token", "").encode()):
            abort(400)

    def registros(empresa_id, tipos):
        return Registro.query.filter(Registro.empresa_id == empresa_id, Registro.tipo.in_(tipos))

    def preferencia(empresa_id):
        item = registros(empresa_id, {"preferencias"}).order_by(Registro.id.desc()).first()
        return item.dados if item else {}

    def registrar(empresa_id, tipo, dados):
        item = Registro(empresa_id=empresa_id, tipo=tipo, dados=dados)
        db.session.add(item)
        db.session.commit()
        return item

    @app.before_request
    def delimitar_acesso_equipe():
        membro_id = session.get("portal_membro")
        if not membro_id:
            return
        membro = db.session.get(Membro, membro_id)
        if not membro or not membro.senha or membro.empresa_id != session.get("empresa_id"):
            session.clear()
            return redirect(url_for("login"))
        permitidos = {"painel_malharia", "teares_form", "cadastrar_teares", "editar_tear", "logout", "static", "api_cidades"}
        if request.endpoint not in permitidos:
            abort(403)
        if request.endpoint == "painel_malharia" and request.args.get("secao", "teares") not in {"teares", "ativos", "inativos"}:
            abort(403)

    @app.route("/portal/equipe/entrar", methods=["GET", "POST"])
    def portal_equipe_login():
        limitar(20)
        if request.method == "POST":
            conferir_csrf()
            email = request.form.get("email", "").strip().lower()
            membro = Membro.query.filter_by(email=email).first()
            senha = request.form.get("senha", "")
            if membro and membro.senha and len(senha) <= 128 and check_password_hash(membro.senha, senha):
                session.clear()
                session.update(empresa_id=membro.empresa_id, portal_membro=membro.id)
                return redirect(url_for("painel_malharia"))
            flash("E-mail ou senha inválidos.")
        return render_template("portal_acesso.html", modo="login", portal_csrf=csrf()), 200, {"Cache-Control": "no-store"}

    @app.route("/portal/equipe/convite/<token>", methods=["GET", "POST"])
    def portal_equipe_convite(token):
        resumo = hashlib.sha256(token.encode()).hexdigest()
        membro = Membro.query.filter_by(convite=resumo).first_or_404()
        if not membro.validade or membro.validade <= datetime.utcnow():
            abort(410)
        if request.method == "POST":
            conferir_csrf()
            senha = request.form.get("senha", "")
            if not 12 <= len(senha) <= 128:
                flash("Use entre 12 e 128 caracteres.")
            else:
                atualizado = Membro.query.filter_by(id=membro.id, convite=resumo).update({
                    Membro.senha: generate_password_hash(senha), Membro.convite: None, Membro.validade: None,
                })
                if not atualizado:
                    abort(410)
                db.session.commit()
                return redirect(url_for("portal_equipe_login"))
        return render_template("portal_acesso.html", modo="convite", portal_csrf=csrf()), 200, {
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
        }

    @app.route("/empresa/<int:empresa_id>/acesso", methods=["GET", "POST"])
    def portal_identificar_visitante(empresa_id):
        limitar(30)
        empresa = Empresa.query.filter(Empresa.id == empresa_id, empresa_publicavel()).first_or_404()
        contato = request.args.get("acao") == "whatsapp"
        if request.method == "POST":
            conferir_csrf()
            nome = request.form.get("nome", "").strip()
            email = request.form.get("email", "").strip().lower()
            telefone = re.sub(r"\D", "", request.form.get("telefone", ""))
            endereco = {campo: request.form.get(campo, "").strip() for campo in ("cep", "endereco", "numero", "cidade", "estado")}
            organizacao = request.form.get("empresa_visitante", "").strip()
            endereco_ok = (re.fullmatch(r"[0-9]{5}-?[0-9]{3}", endereco["cep"])
                           and 0 < len(endereco["endereco"]) <= 200 and 0 < len(endereco["numero"]) <= 20
                           and 0 < len(endereco["cidade"]) <= 100 and re.fullmatch("[a-zA-Z]{2}", endereco["estado"]))
            if endereco_ok and len(organizacao) <= 120 and 2 <= len(nome) <= 100 and len(email) <= 254 and EMAIL.fullmatch(email) and len(telefone) in (10, 11) and request.form.get("autorizar") == "sim":
                registrar(empresa.id, "contato" if contato else "acesso", {"nome": nome, "email": email, "telefone": telefone, "endereco": endereco, "empresa": organizacao})
                if contato:
                    return redirect(url_for("empresa_whatsapp", empresa_id=empresa.id))
                if preferencia(empresa.id).get("email_acessos"):
                    enviar_email(empresa.email, "Novo acesso ao perfil AcheTece",
                                 "<p>Há um novo acesso identificado no Portal da Malharia.</p>")
                return redirect(url_for("empresa_perfil", empresa_id=empresa.id,
                                        pagina=request.args.get("pagina", 1, type=int),
                                        pp=request.args.get("pp", 20, type=int),
                                        **{k: v for k, v in filtros_busca().items() if v}))
            flash("Confira o endereço, nome, e-mail e telefone com DDD e confirme o compartilhamento.")
        return render_template("portal_acesso.html", modo="visitante", contato=contato, empresa=empresa, portal_csrf=csrf()), 200, {
            "Cache-Control": "no-store",
        }

    @app.post("/portal/acao/<secao>")
    def portal_acao(secao):
        limitar(30)
        empresa, _ = identificar()
        if not empresa or session.get("portal_membro"):
            abort(403)
        conferir_csrf()
        tipo = request.form.get("tipo")
        if secao == "perfil" and tipo == "administrador":
            nome = request.form.get("responsavel_nome", "").strip()
            sobrenome = request.form.get("responsavel_sobrenome", "").strip()
            if not 2 <= len(nome) <= 80 or len(sobrenome) > 80 or sum(c.isalpha() for c in nome) < 2:
                flash("Informe um nome válido para o responsável.")
            else:
                empresa.responsavel_nome = nome
                empresa.responsavel_sobrenome = sobrenome or None
                email = request.form.get("responsavel_email", "").strip().lower()
                telefone = re.sub(r"\D", "", request.form.get("responsavel_telefone", ""))
                if (email and (len(email) > 254 or not EMAIL.fullmatch(email))) or (telefone and len(telefone) != 11):
                    db.session.rollback()
                    flash("Confira o e-mail e o telefone do responsável.")
                else:
                    registrar(empresa.id, "dados_administrador", {"email": email, "telefone": telefone})
                    flash("Dados do administrador salvos.")
        elif secao == "equipe" and tipo == "revogar":
            membro_id = request.form.get("membro", type=int)
            Membro.query.filter_by(id=membro_id, empresa_id=empresa.id).delete()
            db.session.commit()
            flash("Acesso removido.")
        elif secao == "equipe" and tipo == "convidar":
            nome = request.form.get("nome", "").strip()
            email = request.form.get("email", "").strip().lower()
            if not (2 <= len(nome) <= 100 and len(email) <= 254 and EMAIL.fullmatch(email)):
                abort(400)
            token = secrets.token_urlsafe(32)
            db.session.add(Membro(empresa_id=empresa.id, email=email, nome=nome,
                                  convite=hashlib.sha256(token.encode()).hexdigest(),
                                  validade=datetime.utcnow() + timedelta(days=2)))
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash("Esse e-mail já está associado a uma equipe.")
            else:
                link = url_for("portal_equipe_convite", token=token, _external=True, _scheme="https")
                empresa_nome = empresa.apelido or empresa.nome
                texto = (
                    f"Olá, {nome}!\n\n"
                    f"{empresa_nome} convidou você para o Portal da Malharia AcheTece.\n"
                    f"Aceite o convite e crie sua senha: {link}\n\n"
                    "O link é de uso único e vence em 48 horas. Depois de criar uma senha com pelo menos "
                    "12 caracteres, entre como membro da equipe com este e-mail e a senha que definiu. "
                    "Você poderá cadastrar e editar teares. Nenhuma etapa adicional de cadastro é necessária.\n\n"
                    "Se você não esperava este convite, ignore a mensagem."
                )
                enviado = enviar_email(email, "Convite para o Portal da Malharia AcheTece",
                                       f'<p>{escape(texto)}</p><p><a href="{escape(link)}">Aceitar convite e criar senha</a></p>', texto)
                if enviado and not app.config.get("MAIL_SUPPRESS_SEND"):
                    flash("Convite encaminhado por e-mail. O link vence em 48 horas.")
                else:
                    flash(f"Envio de e-mail indisponível neste ambiente. Compartilhe o link, válido por 48 horas: {link}")
        elif secao in {"configuracoes", "notificacoes"} and tipo == "preferencias":
            dados = {chave: request.form.get(chave) == "sim" for chave in ("novos_acessos", "assinatura", "email_acessos")}
            registrar(empresa.id, tipo, dados)
            flash("Preferências salvas.")
        elif tipo in SECOES.get(secao, set()):
            titulo = request.form.get("titulo", "").strip()
            mensagem = request.form.get("mensagem", "").strip()
            if not (1 <= len(titulo) <= 100 and 1 <= len(mensagem) <= 2000):
                abort(400)
            dados = {"titulo": titulo, "mensagem": mensagem}
            if tipo == "sugestao":
                categoria = request.form.get("categoria")
                if categoria not in CATEGORIAS_SUGESTAO or len(mensagem) < 20:
                    abort(400, "Selecione uma categoria e descreva a sugestão com pelo menos 20 caracteres.")
                dados["categoria"] = categoria
            item = registrar(empresa.id, tipo, dados)
            enviado = enviar_email("gestao.achetece@gmail.com", f"AcheTece: solicitação #{item.id}",
                                   f"<p>Solicitação #{item.id}, malharia #{empresa.id}.</p><p>{escape(titulo)}</p><p>{escape(mensagem)}</p>",
                                   f"Malharia #{empresa.id}\n{titulo}\n{mensagem}")
            flash("Solicitação registrada." if enviado else "Solicitação registrada. Consulte o suporte por e-mail ou WhatsApp para acompanhamento.")
        else:
            abort(400)
        return redirect(url_for("painel_malharia", secao=secao))

    @app.cli.command("enviar-lembretes-assinatura")
    def enviar_lembretes_assinatura():
        """Enviar um lembrete por vigência, nos sete dias anteriores ao fim."""
        if app.config.get("MAIL_SUPPRESS_SEND"):
            return
        agora = datetime.utcnow()
        ids = db.session.query(Empresa.id).filter(empresa_publicavel()).all()
        for empresa_id, in ids:
            empresa = Empresa.query.filter(Empresa.id == empresa_id, empresa_publicavel()).with_for_update().first()
            if empresa:
                fim = empresa.assinatura_expira_em
                if fim and agora < fim <= agora + timedelta(days=7):
                    anterior = registros(empresa_id, {"lembrete_vigencia"}).order_by(Registro.id.desc()).first()
                    if not anterior or anterior.dados.get("vigencia") != fim.isoformat():
                        corpo = (
                            f"Olá! Sua assinatura AcheTece está vigente até {fim:%d/%m/%Y}. "
                            "Renove pelo Financeiro no Portal da Malharia para manter seus teares visíveis na busca."
                        )
                        if enviar_email(empresa.email, "Sua assinatura AcheTece vence em breve",
                                        f"<p>{escape(corpo)}</p>", corpo):
                            registrar(empresa_id, "lembrete_vigencia", {"vigencia": fim.isoformat()})
            db.session.rollback()

    def dados_cadastrais(empresa_id, tipo):
        registro = registros(empresa_id, {tipo}).order_by(Registro.id.desc()).first()
        return registro.dados if registro else {}

    def guardar_empresa(empresa_id, dados):
        db.session.add(Registro(empresa_id=empresa_id, tipo="dados_empresa", dados=dados))

    def contexto(empresa_id, secao):
        if session.get("portal_membro"):
            return {}
        dados = {"portal_csrf": csrf()}
        if secao == "equipe":
            dados["membros"] = Membro.query.filter_by(empresa_id=empresa_id).order_by(Membro.nome).all()
        if secao == "sugestoes":
            dados["categorias_sugestao"] = CATEGORIAS_SUGESTAO
        if secao in SECOES:
            dados["historico"] = registros(empresa_id, SECOES[secao]).order_by(Registro.id.desc()).limit(50).all()
        if secao == "perfil":
            dados["administrador"] = dados_cadastrais(empresa_id, "dados_administrador")
        if secao in {"acessos", "visitas", "contatos"}:
            tipo = "contato" if secao == "contatos" else "acesso"
            dados["acessos"] = registros(empresa_id, {tipo}).filter(
                Registro.criado_em >= datetime.utcnow() - timedelta(days=90),
            ).order_by(Registro.id.desc()).limit(100).all()
        if secao in {"configuracoes", "notificacoes"}:
            dados["preferencias"] = preferencia(empresa_id)
            if dados["preferencias"].get("novos_acessos"):
                dados["novos_acessos"] = registros(empresa_id, {"acesso"}).filter(
                    Registro.criado_em >= datetime.utcnow() - timedelta(days=7),
                ).count()
        return dados

    return contexto, dados_cadastrais, guardar_empresa
