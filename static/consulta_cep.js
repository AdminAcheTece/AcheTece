const cep = document.querySelector('#cep');
const formulario = cep?.form;
if (formulario) {
  const { estado, cidade, endereco, rua, bairro } = formulario.elements;
  const aviso = document.createElement('small');
  aviso.setAttribute('role', 'status');
  aviso.id = 'cep-status';
  cep.setAttribute('aria-describedby', aviso.id);
  cep.insertAdjacentElement('afterend', aviso);
  let consulta;
  let cepConsultado = cep.value.replace(/\D/g, '');
  cep.addEventListener('input', () => {
    consulta?.abort();
    aviso.textContent = '';
  });
  cep.addEventListener('blur', async () => {
    const codigo = cep.value.replace(/\D/g, '');
    if (codigo.length !== 8 || codigo === cepConsultado) return;
    consulta?.abort();
    const atual = new AbortController();
    consulta = atual;
    const timeout = setTimeout(() => atual.abort(), 5000);
    const valores = [estado.value, cidade.value, endereco?.value, rua?.value, bairro?.value];
    aviso.textContent = 'Consultando CEP…';
    try {
      const resposta = await fetch('https://viacep.com.br/ws/' + codigo + '/json/', {
        signal: atual.signal, referrerPolicy: 'no-referrer',
      });
      if (!resposta.ok) throw Error();
      const dados = await resposta.json();
      if (dados.erro || !dados.uf || !dados.localidade) throw Error();
      if (consulta !== atual || cep.value.replace(/\D/g, '') !== codigo) return;
      if (estado.value === valores[0] && cidade.value === valores[1]) {
        estado.value = dados.uf;
        if (cidade instanceof HTMLSelectElement) {
          cidade.dataset.cepCidade = dados.localidade;
          estado.dispatchEvent(new Event('change'));
          delete cidade.dataset.cepCidade;
        } else {
          cidade.value = dados.localidade;
        }
      }
      if (endereco && endereco.value === valores[2] && dados.logradouro) {
        endereco.value = [dados.logradouro, dados.bairro].filter(Boolean).join(', ');
      }
      if (rua && rua.value === valores[3]) rua.value = dados.logradouro || '';
      if (bairro && bairro.value === valores[4]) bairro.value = dados.bairro || '';
      cepConsultado = codigo;
      aviso.textContent = 'Confira os dados e informe o número e o complemento, se aplicável.';
    } catch {
      if (consulta === atual && cep.value.replace(/\D/g, '') === codigo) {
        aviso.textContent = 'CEP indisponível. Você pode preencher a localização manualmente.';
      }
    } finally {
      clearTimeout(timeout);
    }
  });
}
