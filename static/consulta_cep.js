// CEP: consulta ao completar oito digitos; preenchimento manual sempre permitido.
(() => {
  const cep = document.querySelector('#cep');
  const form = cep?.form;
  if (!form) return;
  const { estado, cidade, endereco, rua, bairro } = form.elements;
  if (!estado || !cidade) return;
  const aviso = document.createElement('small');
  aviso.setAttribute('role', 'status');
  aviso.id = 'cep-status';
  cep.setAttribute('aria-describedby', aviso.id);
  cep.insertAdjacentElement('afterend', aviso);
  const digitos = () => cep.value.replace(/\D/g, '');
  let timer, controller, consultado = '', emConsulta = '';
  let geracao = 0;
  const campos = [estado, cidade, endereco, rua, bairro].filter(Boolean);
  const automaticos = new Map();
  function cancelar() {
    clearTimeout(timer);
    controller?.abort();
    geracao++;
    emConsulta = '';
  }
  function limparAutomaticos() {
    for (const [campo, valor] of automaticos) {
      if (campo.value === valor) campo.value = '';
    }
    automaticos.clear();
  }
  async function consultar() {
    const codigo = digitos();
    if (codigo.length !== 8 || codigo === consultado || codigo === emConsulta) return;
    cancelar();
    const atual = ++geracao;
    const controle = new AbortController();
    controller = controle;
    emConsulta = codigo;
    const prazo = setTimeout(() => controle.abort(), 8000);
    const antes = new Map(campos.map(c => [c, c.value]));
    const editados = new Set();
    const marcar = e => editados.add(e.currentTarget);
    campos.forEach(c => c.addEventListener('input', marcar));
    campos.forEach(c => c.addEventListener('change', marcar));
    const intacto = c => c && !editados.has(c) && c.value === antes.get(c);
    function preencher(c, valor) {
      if (intacto(c) && valor) { c.value = valor; automaticos.set(c, c.value); }
    }
    aviso.textContent = 'Consultando CEP…';
    try {
      const resposta = await fetch('https://viacep.com.br/ws/' + codigo + '/json/', {
        signal: controle.signal, referrerPolicy: 'no-referrer'
      });
      if (!resposta.ok) throw Error('HTTP');
      const dados = await resposta.json();
      if (dados.erro || !dados.uf || !dados.localidade) throw Error('CEP');
      if (atual !== geracao || digitos() !== codigo) return;
      if (intacto(estado) && intacto(cidade)) {
        estado.value = dados.uf;
        automaticos.set(estado, estado.value);
        if (cidade instanceof HTMLSelectElement) {
          cidade.dataset.cepCidade = dados.localidade;
          estado.dispatchEvent(new Event('change'));
          delete cidade.dataset.cepCidade;
          if (cidade.value) automaticos.set(cidade, cidade.value);
        } else preencher(cidade, dados.localidade);
      }
      preencher(endereco, [dados.logradouro, dados.bairro].filter(Boolean).join(', '));
      preencher(rua, dados.logradouro);
      preencher(bairro, dados.bairro);
      consultado = codigo;
      aviso.textContent = 'Confira o endereço. Complete os campos que faltarem e informe o número.';
    } catch {
      if (atual === geracao && digitos() === codigo) {
        aviso.textContent = 'Não foi possível consultar o CEP. Tente novamente ou preencha manualmente.';
      }
    } finally {
      clearTimeout(prazo);
      campos.forEach(c => c.removeEventListener('input', marcar));
      campos.forEach(c => c.removeEventListener('change', marcar));
      if (atual === geracao) emConsulta = '';
    }
  }
  cep.addEventListener('input', () => {
    cancelar();
    if (digitos() !== consultado) { limparAutomaticos(); consultado = ''; }
    aviso.textContent = '';
    if (digitos().length === 8) timer = setTimeout(consultar, 300);
  });
  cep.addEventListener('blur', () => { clearTimeout(timer); consultar(); });
  const tentar = document.createElement('button');
  tentar.type = 'button';
  tentar.textContent = 'Consultar CEP';
  tentar.addEventListener('click', () => { consultado = ''; cancelar(); consultar(); });
  aviso.insertAdjacentElement('afterend', tentar);
})();
