const interval = document.getElementById('intervalSelect');

if (interval) {
  interval.value = '5m';
  if (typeof interval.onchange === 'function') {
    await interval.onchange();
  }
}
