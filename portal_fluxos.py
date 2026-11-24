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
    "configuracoes": {"sugestao"},
    "financeiro": {"troca_plano", "cancelamento"},
}
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
        if request.method == "POST":
            conferir_csrf()
            nome = request.form.get("nome", "").strip()
            email = request.form.get("email", "").strip().lower()
            if 2 <= len(nome) <= 100 and len(email) <= 254 and EMAIL.fullmatch(email) and request.form.get("autorizar") == "sim":
                registrar(empresa.id, "acesso", {"nome": nome, "email": email})
                if preferencia(empresa.id).get("email_acessos"):
                    enviar_email(empresa.email, "Novo acesso ao perfil AcheTece",
                                 "<p>Há um novo acesso identificado no Portal da Malharia.</p>")
                return redirect(url_for("empresa_perfil", empresa_id=empresa.id,
                                        pagina=request.args.get("pagina", 1, type=int),
                                        pp=request.args.get("pp", 20, type=int),
                                        **{k: v for k, v in filtros_busca().items() if v}))
            flash("Preencha nome, e-mail e confirme o compartilhamento.")
        return render_template("portal_acesso.html", modo="visitante", empresa=empresa, portal_csrf=csrf()), 200, {
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
        if secao == "equipe" and tipo == "revogar":
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
                flash(f"Envie este convite ao membro. Válido por 48 horas: {link}")
        elif secao == "configuracoes" and tipo == "preferencias":
            dados = {chave: request.form.get(chave) == "sim" for chave in ("novos_acessos", "assinatura", "email_acessos")}
            registrar(empresa.id, tipo, dados)
            flash("Preferências salvas.")
        elif tipo in SECOES.get(secao, set()):
            titulo = request.form.get("titulo", "").strip()
            mensagem = request.form.get("mensagem", "").strip()
            if not (1 <= len(titulo) <= 100 and 1 <= len(mensagem) <= 2000):
                abort(400)
            item = registrar(empresa.id, tipo, {"titulo": titulo, "mensagem": mensagem})
            enviado = enviar_email("gestao.achetece@gmail.com", f"AcheTece: solicitação #{item.id}",
                                   f"<p>Solicitação #{item.id}, malharia #{empresa.id}.</p><p>{escape(titulo)}</p><p>{escape(mensagem)}</p>",
                                   f"Malharia #{empresa.id}\n{titulo}\n{mensagem}")
            flash("Solicitação registrada." if enviado else "Solicitação registrada. Consulte o suporte por e-mail ou WhatsApp para acompanhamento.")
        else:
            abort(400)
        return redirect(url_for("painel_malharia", secao=secao))

    def contexto(empresa_id, secao):
        if session.get("portal_membro"):
            return {}
        dados = {"portal_csrf": csrf()}
        if secao == "equipe":
            dados["membros"] = Membro.query.filter_by(empresa_id=empresa_id).order_by(Membro.nome).all()
        if secao in SECOES:
            dados["historico"] = registros(empresa_id, SECOES[secao]).order_by(Registro.id.desc()).limit(50).all()
        if secao == "acessos":
            dados["acessos"] = registros(empresa_id, {"acesso"}).filter(
                Registro.criado_em >= datetime.utcnow() - timedelta(days=90),
            ).order_by(Registro.id.desc()).limit(100).all()
        if secao == "configuracoes":
            dados["preferencias"] = preferencia(empresa_id)
            if dados["preferencias"].get("novos_acessos"):
                dados["novos_acessos"] = registros(empresa_id, {"acesso"}).filter(
                    Registro.criado_em >= datetime.utcnow() - timedelta(days=7),
                ).count()
        return dados

    return contexto
