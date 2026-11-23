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
