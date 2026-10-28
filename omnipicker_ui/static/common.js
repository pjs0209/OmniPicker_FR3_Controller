'use strict';
window.UI = (() => {
  const $ = id => document.getElementById(id);
  let toastTimer;
  function toast(message, error = false) {
    $('toast').textContent = message;
    $('toast').className = 'toast' + (error ? ' error' : '');
    $('toast').hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { $('toast').hidden = true; }, error ? 10000 : 5000);
  }
  async function api(path, data, timeout=2500) {
    const response = await fetch((window.API_BASE || '/api/') + path, {
      method: data === undefined ? 'GET' : 'POST', cache: 'no-store',
      headers: {'Content-Type': 'application/json', 'X-Control-Token': window.CONTROL_TOKEN},
      ...(data === undefined ? {signal: AbortSignal.timeout(timeout)} : {body: JSON.stringify(data)})
    });
    const result = await response.json();
    if (!response.ok || result.error) throw new Error(result.error || '서버 응답 오류 ' + response.status);
    return result;
  }
  function badge(id, message, online) {
    $(id).textContent = message;
    $(id).className = 'badge' + (online ? ' online' : '');
  }
  function download(name, data) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'}));
    const link = document.createElement('a');
    link.href = url; link.download = name; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  let eventSignature = '';
  function events(items) {
    const signature = items.map(event => event.id).join(',');
    if (signature === eventSignature) return;
    eventSignature = signature;
    $('events').replaceChildren();
    items.forEach(event => {
      const row = document.createElement('div'); row.className = 'event ' + event.level;
      const time = document.createElement('time');
      time.dateTime = new Date(event.time * 1000).toISOString();
      time.textContent = new Date(event.time * 1000).toLocaleTimeString('ko-KR', {hour12: false});
      const source = document.createElement('span'); source.className = 'event-source'; source.textContent = event.source;
      const text = document.createElement('span'); text.textContent = event.message;
      row.append(time, source, text); $('events').append(row);
    });
  }
  function errorBanner(message) {
    $('connectionError').hidden = !message;
    $('connectionError').textContent = message || '';
  }
  function clock() { $('clock').textContent = new Date().toLocaleTimeString('ko-KR', {hour12: false}); }
  clock(); setInterval(clock, 1000);
  return {$, toast, api, badge, download, events, errorBanner};
})();
