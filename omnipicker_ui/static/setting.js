import {RobotScene} from './robot-scene.js';
(() => {
  const {$, api, toast, badge, events, errorBanner, download} = UI;
  let state = null, pending = false, polling = false, initialized = false, logs = [];
  let viewer = null;
  try { viewer = new RobotScene($('usbViewer'), {gripperOnly: true}); }
  catch (error) { toast(error.message, true); }
  $('usbViewer').addEventListener('modelerror', event => toast(event.detail, true));
  function updateViewer() { viewer?.update({gripper: {actual_position: null}}, null, Number($('position').value)/100); }
  $('usbViewer').addEventListener('modelloaded', updateViewer);
  let dialogAction = null;
  const fields = ['nodeId', 'baudrate', 'rs485Mode', 'modbusMode', 'stopbits', 'enableOnBoot'];
  function values() { return {node_id: Number($('nodeId').value), baudrate: Number($('baudrate').value),
    rs485_mode: $('rs485Mode').value === 'true', modbus_mode: $('modbusMode').value === 'true',
    stopbits: Number($('stopbits').value), enable_on_boot: $('enableOnBoot').value === 'true'}; }
  function fill() {
    const settings = state?.settings;
    if (!settings) return;
    const mapping = {nodeId: 'node_id', baudrate: 'baudrate', rs485Mode: 'rs485_mode', modbusMode: 'modbus_mode', stopbits: 'stopbits', enableOnBoot: 'enable_on_boot'};
    for (const [id, key] of Object.entries(mapping)) {
      const value = String(settings[key]);
      // Preserve unexpected values returned by the device for visibility.
      if ($(id).tagName === 'SELECT' && ![...$(id).options].some(option => option.value === value)) {
        const option = document.createElement('option'); option.value = value; option.textContent = `현재 값: ${value}`; $(id).append(option);
      }
      $(id).value = value;
    }
    initialized = true;
  }
  function dirty() {
    if (!state?.settings) return false;
    const current = values();
    return Object.keys(current).some(key => current[key] !== state.settings[key]);
  }
  function render() {
    const connected = !!state?.connected, busy = pending || !!state?.busy;
    $('connectionDot').className = 'dot' + (connected ? ' online' : '');
    $('connectionLabel').textContent = busy ? 'USB 처리 중…' : connected ? 'USB 연결됨' : 'USB 연결 대기';
    $('connect').disabled = connected || busy;
    $('scan').disabled = connected || busy;
    $('device').disabled = connected || busy;
    ['refresh', 'disconnect', 'fr3Preset', ...fields].forEach(id => { $(id).disabled = !connected || busy; });
    ['applySettings', 'saveFlash', 'exportSettings'].forEach(id => { $(id).disabled = !connected || busy; });
    $('formStatus').textContent = connected ? 'USB 연결됨' : '미연결';
    $('formDot').className = 'dot' + (connected ? ' online' : '');
    badge('deviceBadge', connected ? '연결됨' : '미연결', connected);
    badge('saveBadge', state?.unsaved ? '미저장 변경 있음' : '장치 설정', !state?.unsaved && connected);
    $('dirtyHint').textContent = dirty() ? '편집됨 · RAM 미적용' : state?.unsaved ? 'RAM 적용됨 · 플래시 미저장' : '';
    const info = connected ? state.info : null;
    $('serial').textContent = info?.serial_number ?? '—';
    $('firmware').textContent = info?.firmware_hex ?? '—';
    $('motorError').textContent = info ? String(info.error_code) : '—';
    $('calibrated').textContent = info ? info.calibrated ? '완료' : '미완료' : '—';
    $('limits').textContent = info ? `${Number(info.current_limit).toFixed(2)} A / ${Number(info.velocity_limit).toFixed(2)} rad/s` : '—';
    $('schemaCrc').textContent = info?.schema_crc ?? '—';
    $('lastRead').textContent = state?.updated ? new Date(state.updated * 1000).toLocaleTimeString('ko-KR', {hour12: false}) : '—';
    badge('testBadge', state?.armed ? `테스트 허용 · ${state.seconds}초` : '동작 잠금', !!state?.armed);
    $('enable').disabled = !connected || busy || !!state?.armed;
    $('sendMove').disabled = !connected || busy || !state?.armed;
    $('disableMotor').disabled = !connected || busy;
    ['position', 'kp', 'kd'].forEach(id => { $(id).disabled = busy; });
    $('lastTarget').textContent = state?.target == null ? '—' : `${Math.round(state.target * 100)}% · 도달 미확인`;
    if (state?.events) { logs = state.events; events(logs); }
    errorBanner(state?.error || '');
  }
  async function refresh(forceFill = false) {
    if (polling) return;
    polling = true;
    try { state = await api('status'); if ((!initialized || forceFill) && state.connected) fill(); render(); }
    catch (error) { state = null; initialized = false; render(); errorBanner('로컬 USB 서버 응답 없음 · 조작 잠금. 실행 터미널을 확인하세요.'); }
    finally { polling = false; }
  }
  async function command(path, data = {}, forceFill = false) {
    if (pending) return;
    pending = true; render();
    try { await api(path, data); }
    catch (error) { toast(error.message, true); }
    finally { pending = false; await refresh(forceFill); render(); }
  }
  function confirmAction(title, description, detail, action) {
    $('dialogTitle').textContent = title;
    $('dialogDescription').textContent = description;
    $('dialogDetail').textContent = detail;
    dialogAction = action; $('confirmDialog').showModal();
  }
  $('dialogCancel').onclick = () => { dialogAction = null; $('confirmDialog').close(); };
  $('dialogConfirm').onclick = () => {
    const action = dialogAction; dialogAction = null; $('confirmDialog').close(); if (action) action();
  };
  async function scan() {
    if (pending) return;
    pending = true; render();
    try {
      const result = await api('devices'); $('device').replaceChildren();
      result.devices.forEach(device => {
        const option = document.createElement('option'); option.value = JSON.stringify({bus: device.bus, address: device.address}); option.textContent = device.label; $('device').append(option);
      });
      if (!result.devices.length) { const option = document.createElement('option'); option.textContent = 'USB 장치 없음'; option.value = ''; $('device').append(option); toast('OmniPicker 전원과 USB 데이터 케이블을 확인하세요.', true); }
    } catch (error) { toast(error.message, true); }
    finally { pending = false; render(); }
  }
  $('scan').onclick = scan;
  $('connect').onclick = () => {
    const device = $('device').value;
    if (!device) return toast('장치 검색 후 연결할 USB 장치를 선택하세요.', true);
    initialized = false; command('connect', JSON.parse(device), true);
  };
  $('disconnect').onclick = () => confirmAction('모터 비활성화 후 연결 해제', '모터 비활성화 시 파지력이 해제될 수 있습니다.', '물체를 내려놓고 진행하세요.', () => command('disconnect'));
  $('refresh').onclick = () => {
    if (dirty()) confirmAction('장치 설정 다시 읽기', '화면에서 편집한 값을 장치의 현재 값으로 바꿉니다.', '아직 적용하지 않은 편집 내용은 사라집니다.', () => command('refresh', {}, true));
    else command('refresh', {}, true);
  };
  $('fr3Preset').onclick = () => {
    $('nodeId').value = 1; $('baudrate').value = '115200'; $('rs485Mode').value = 'true';
    $('modbusMode').value = 'true'; $('stopbits').value = '0'; render();
    toast('FR3 프리셋 입력됨');
  };
  fields.forEach(id => $(id).oninput = render);
  $('applySettings').onclick = () => {
    const settings = values();
    confirmAction('USB 통신 설정 적용', '모터를 비활성화한 다음 설정을 RAM에 적용합니다.',
      `주소: ${settings.node_id}\n물리 모드: ${settings.rs485_mode ? 'RS485' : 'UART TTL'}\n프로토콜: ${settings.modbus_mode ? 'Modbus RTU' : 'OmniPicker 전용'}\n통신 속도: ${settings.baudrate} bps\n정지 비트: ${[1, 1.5, 2][settings.stopbits]}\n전원 인가 시 활성화: ${settings.enable_on_boot ? '켜기' : '끄기'}`,
      () => command('settings', {settings, confirm: 'APPLY_SETTINGS'}, true));
  };
  $('saveFlash').onclick = () => {
    if (dirty()) return toast('화면의 편집 내용을 먼저 RAM에 적용하세요.', true);
    confirmAction('장치 플래시에 영구 저장', '현재 장치 설정을 저장합니다. 저장 중 USB 연결이 끊길 수 있습니다.',
      '저장 후 전원을 다시 켜고 USB 재연결 → 설정 재조회로 값을 확인하세요.\n화면 값을 바꾸기만 한 경우에는 장치에 저장되지 않습니다.',
      () => { initialized = false; command('save', {confirm: 'SAVE_FLASH'}); });
  };
  $('exportSettings').onclick = () => download('OmniPicker_settings.json', {info: state.info, settings: state.settings, ranges: state.ranges, read_at: state.updated, unsaved: state.unsaved});
  $('exportLog').onclick = () => download('OmniPicker_USB_log.json', logs);
  function position() {
    $('positionOut').textContent = $('position').value;
    document.querySelectorAll('[data-position]').forEach(button => button.classList.toggle('selected', button.dataset.position === $('position').value));
    updateViewer();
  }
  $('position').oninput = position;
  document.querySelectorAll('[data-position]').forEach(button => button.onclick = () => { $('position').value = button.dataset.position; position(); });
  $('enable').onclick = () => {
    if (!$('areaClear').checked) return toast('작업영역과 RS485 송신 중지를 확인하세요.', true);
    $('areaClear').checked = false; command('enable', {confirm: 'PHYSICAL_AREA_CLEAR'});
  };
  $('lock').onclick = () => { api('lock', {}).then(() => refresh()).catch(error => toast(error.message, true)); };
  $('sendMove').onclick = () => command('move', {position: Number($('position').value) / 100, kp: Number($('kp').value), kd: Number($('kd').value)});
  $('disableMotor').onclick = () => confirmAction('모터 비활성화', '모터를 끄면 물체가 떨어질 수 있습니다.', '물체를 내려놓고 실행하세요.', () => command('disable'));
  position(); render(); refresh(); setInterval(refresh, 1000);
})();
