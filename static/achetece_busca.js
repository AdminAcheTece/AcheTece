const searchForm = document.getElementById('filtros');
const stateField = document.getElementById('estado');
const cityField = document.getElementById('cidade');
const citiesByState = JSON.parse(document.getElementById('cidades-por-uf').textContent);
const searchStatus = document.getElementById('busca-status');

stateField.addEventListener('change', () => {
  const state = stateField.value;
  cityField.replaceChildren(new Option(state ? 'Todas' : 'Selecione o estado', ''));
  for (const city of citiesByState[state] ?? []) {
    cityField.add(new Option(city, city));
  }
  cityField.disabled = !state;
});

searchForm.addEventListener('submit', () => {
  searchStatus.textContent = 'Buscando teares…';
});
window.addEventListener('pageshow', () => {
  searchStatus.textContent = '';
});

const cadastro = document.getElementById('cadastro-visitante');
if (cadastro && typeof cadastro.showModal === 'function') {
  document.querySelectorAll('[data-identificar]').forEach(link => {
    link.addEventListener('click', event => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      cadastro.querySelector('form').action = link.href;
      cadastro.querySelector('[data-destino]').textContent = link.dataset.identificar;
      cadastro.querySelector('[type=submit]').textContent =
        link.classList.contains('contact-link') ? 'Abrir WhatsApp' : 'Ver malharia';
      cadastro.showModal();
    });
  });
  cadastro.querySelector('[data-fechar]').addEventListener('click', () => cadastro.close());
}
