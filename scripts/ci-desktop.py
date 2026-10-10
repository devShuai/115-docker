#!/usr/bin/env python3
"""Real Chromium connection, resize and equal-scene benchmark for both backends."""
import base64
import csv
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from cdp import CDP

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.ci-artifacts'
PASSWORD = 'Check123'  # Synthetic CI profiles only.


def wait_for(fn, label, seconds=60):
    deadline, last = time.monotonic() + seconds, None
    while time.monotonic() < deadline:
        try:
            result = fn()
            if result:
                return result
        except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
            last = error
        time.sleep(.2)
    raise RuntimeError(f'Timed out: {label}; last error: {last}')


def page_client(port, match):
    def find():
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/json', timeout=2) as response:
            pages = json.load(response)
        for page in pages:
            if page.get('type') == 'page' and (match == page.get('id') or match in page.get('url', '')):
                return CDP(page['webSocketDebuggerUrl'])
    return wait_for(find, f'Chromium debug page on {port}')


def process_stats(root_pid):
    entries = {}
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            raw = path.read_text()
            fields = raw[raw.rindex(')') + 2:].split()
            entries[int(path.parent.name)] = (int(fields[1]), int(fields[11]) + int(fields[12]), raw[raw.index('(')+1:raw.rindex(')')])
        except (OSError, ValueError, IndexError):
            continue
    tree = {root_pid}
    while True:
        children = {pid for pid, (ppid, _, _) in entries.items() if ppid in tree}
        if children <= tree:
            break
        tree |= children
    totals = dict(ticks=0, pss=0, remote_ticks=0, remote_pss=0)
    for pid in tree:
        if pid not in entries:
            continue
        _, cpu, name = entries[pid]
        memory = 0
        try:
            text = Path(f'/proc/{pid}/smaps_rollup').read_text()
            memory = int(re.search(r'^Pss:\s+(\d+)', text, re.M)[1])*1024
        except (OSError, TypeError):
            pass
        totals['ticks'] += cpu
        totals['pss'] += memory
        if name in ('Xkasmvnc', 'Xtigervnc', 'python3'):
            totals['remote_ticks'] += cpu
            totals['remote_pss'] += memory
    return totals


def desktop_size(env):
    text = subprocess.check_output(['xrandr', '--current'], env=env, text=True)
    match = re.search(r'current (\d+) x (\d+)', text)
    return [int(match[1]), int(match[2])]


def canvas_sample(client):
    # Sparse readback proves pixels change without a per-frame GPU readback loop.
    return client.evaluate("""(()=>{
      const c=Array.from(document.querySelectorAll('#noVNC_container canvas')).find(c=>c.offsetParent!==null);
      if(!c)return null;const ctx=c.getContext('2d');if(!ctx)return null;
      return [[.30,.47],[.21,.31],[.72,.61],[.60,.73]].map(([x,y])=>Array.from(ctx.getImageData(Math.floor(c.width*x),Math.floor(c.height*y),1,1).data).join(',')).join(';');
    })()""")


def stop_group(process):
    if process and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def verify_auth(backend):
    if backend != 'kasm':
        return
    for path in ('/vnc.html', '/websockify'):
        for credentials in (None, '115:Wrong123'):
            headers = {}
            if path == '/websockify':
                for name,value in {'Upgrade':'websocket','Connection':'Upgrade','Sec-WebSocket-Version':'13',
                                   'Sec-WebSocket-Key':'MDEyMzQ1Njc4OWFiY2RlZg=='}.items():
                    headers[name] = value
            if credentials:
                headers['Authorization'] = 'Basic '+base64.b64encode(credentials.encode()).decode()
            # Keep deliberate failed logins off the real client's IP. Kasm's
            # production brute-force protection remains enabled in this test.
            connection = http.client.HTTPConnection('127.0.0.1',1150,timeout=3,source_address=('127.0.0.2',0))
            try:
                connection.request('GET',path,headers=headers)
                response = connection.getresponse()
                assert response.status == 401, f'KasmVNC accepted missing/wrong credentials: {path} {response.status}'
                response.read()
            finally:
                connection.close()
    try:
        connection = socket.create_connection(('127.0.0.1', 6015), timeout=1)
    except OSError:
        pass
    else:
        connection.close()
        raise AssertionError('KasmVNC exposed an unnecessary raw RFB port')


def run_backend(backend):
    client_port = 9223 if backend == 'legacy' else 9225
    server_port = client_port+1
    profile = Path(f'/tmp/115-ci-{backend}')
    shutil.rmtree(profile, ignore_errors=True)
    env = {**os.environ, 'PASSWORD': PASSWORD, 'REMOTE_USER': '115', 'REMOTE_BACKEND': backend,
           'AUTO_START': 'true', 'ENABLE_COOKIE_EXTENSION': 'false', 'DISPLAY': ':115',
           'DISPLAY_WIDTH': '1600', 'DISPLAY_HEIGHT': '900', 'VNC_FPS': '30', 'KASM_THREADS': '2',
           'PROFILE_DIR_IN_CONTAINER': str(profile/'server'), 'RUNTIME_DIR': '/run/115'}
    profile.mkdir(parents=True)
    launcher = profile/'launch.sh'
    launcher.write_text(f'#!/bin/bash\nexec /opt/115/115.sh --remote-debugging-port={server_port} '
                        '--disable-background-networking --disable-sync --disable-component-update --disable-dev-shm-usage '
                        '--remote-allow-origins=* --app="file://'+str(ROOT/'scripts/perf-fixture.html')+'"\n')
    launcher.chmod(0o700)
    env['BROWSER_LAUNCHER'] = str(launcher)
    log = (OUT/f'{backend}-desktop.log').open('w')
    client_log = (OUT/f'{backend}-client.log').open('w')
    desktop = client_process = client = fixture = None
    try:
        desktop = subprocess.Popen(['/opt/115/entrypoint.sh'], env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        def healthy():
            assert desktop.poll() is None, 'Desktop exited'
            return subprocess.run(['python3', '/opt/115/healthcheck.py'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        wait_for(healthy, f'{backend} health')
        verify_auth(backend)
        fixture = page_client(server_port, 'perf-fixture.html')
        wait_for(lambda: fixture.evaluate('!!window.benchmark'), 'visible browser fixture')
        if backend == 'legacy':
            subprocess.run(['python3', str(ROOT/'scripts/ci-rfb.py')], env=env, check=True)
        client_process = subprocess.Popen([
            '/usr/local/115Browser/115Browser', '--headless=old', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
            '--no-first-run', '--no-default-browser-check', '--disable-background-networking',
            '--disable-background-timer-throttling', '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows',
            '--disable-sync', '--disable-component-update', f'--remote-debugging-port={client_port}',
            '--remote-allow-origins=*', '--disable-extensions', f'--user-data-dir={profile/"client"}', 'about:blank'],
            env=env, cwd='/usr/local/115Browser', stdout=client_log, stderr=subprocess.STDOUT, start_new_session=True)
        # Chromium 125's original headless mode bypasses 115's home-tab startup
        # logic. The normal visible desktop still runs the full 115 application.
        client = page_client(client_port, 'about:blank')
        received = dict(bytes=0, messages=0, errors=[], network_errors=[])
        def event(message):
            method, params = message.get('method'), message.get('params', {})
            if method == 'Fetch.authRequired':
                client.send('Fetch.continueWithAuth', {'requestId': params['requestId'], 'authChallengeResponse':
                            {'response': 'ProvideCredentials', 'username': '115', 'password': PASSWORD}})
            elif method == 'Fetch.requestPaused':
                client.send('Fetch.continueRequest', {'requestId': params['requestId']})
            elif method == 'Network.webSocketFrameReceived':
                frame = params['response']
                payload = frame['payloadData']
                received['bytes'] += len(base64.b64decode(payload)) if frame['opcode'] == 2 else len(payload.encode())
                received['messages'] += 1
            elif method == 'Runtime.exceptionThrown':
                detail = params.get('exceptionDetails', {})
                received['errors'].append(detail.get('exception',{}).get('description',detail.get('text','JS error')))
            elif method == 'Log.entryAdded' and params['entry']['level'] in ('error','warning'):
                received['errors'].append(params['entry']['text'])
            elif method == 'Network.loadingFailed':
                received['network_errors'].append(params)
        client.handlers.append(event)
        client.call('Page.enable')
        client.call('Runtime.enable')
        client.call('Network.enable')
        client.call('Log.enable')
        if backend == 'kasm':
            client.call('Fetch.enable', {'handleAuthRequests': True})
        client.call('Emulation.setDeviceMetricsOverride', dict(width=1600, height=900, deviceScaleFactor=1, mobile=False))
        client.call('Page.navigate', {'url': 'http://127.0.0.1:1150/'})
        def connected():
            if backend == 'legacy':
                client.evaluate("(()=>{const d=document.querySelector('#noVNC_credentials_dlg');const p=document.querySelector('#noVNC_password_input');if(d?.classList.contains('noVNC_open')&&p&&p.offsetParent!==null&&!window.ciCredentialsSent){window.ciCredentialsSent=true;p.value='Check123';document.querySelector('#noVNC_credentials_button').click();}})()")
            return client.evaluate("document.documentElement?.classList.contains('noVNC_connected')")
        wait_for(connected, f'{backend} real web client connection')
        sizes = []
        for width, height in ((1024,768), (1280,720), (1920,1080), (1600,900)):
            client.call('Emulation.setDeviceMetricsOverride', dict(width=width, height=height, deviceScaleFactor=1, mobile=False))
            def resized():
                client.pump(.1)
                painted = client.evaluate("Array.from(document.querySelectorAll('#noVNC_container canvas')).filter(c=>c.offsetParent!==null).map(c=>[c.width,c.height])")
                return [width,height] in painted and desktop_size(env) == [width,height]
            wait_for(resized, f'{backend} framebuffer and X11 {width}x{height}', seconds=25)
            sizes.append([width,height])
            layout = fixture.evaluate('[innerWidth,innerHeight]')
            assert layout[0] == width and height/2 < layout[1] <= height, f'Maximized browser did not follow desktop: {layout}'
            client.pump(.5)
            # Export the received framebuffer itself. This also works when a
            # CI headless compositor cannot expose a Page screenshot surface.
            shot = client.evaluate("Array.from(document.querySelectorAll('#noVNC_container canvas')).find(c=>c.offsetParent!==null&&c.width===innerWidth&&c.height===innerHeight).toDataURL('image/png')")
            png = base64.b64decode(shot.split(',',1)[1])
            assert png.startswith(b'\x89PNG\r\n\x1a\n') and len(png)>1024, 'Invalid desktop screenshot'
            (OUT/f'{backend}-{width}x{height}.png').write_bytes(png)
            print(f'PASS: {backend} web canvas + actual X11 resize {width}x{height}', flush=True)
        # Startup home tabs may appear after the fixture's initial load.
        # Close them after resize verification so the benchmark has no web content.
        for info in fixture.call('Target.getTargets')['targetInfos']:
            if info['type'] == 'page' and 'perf-fixture.html' not in info['url']:
                fixture.call('Target.closeTarget', {'targetId':info['targetId']})
        measurements = []
        for phase, dynamic, duration in (('idle',False,8), ('animation',True,15)):
            fixture.evaluate(f'window.benchmark.setDynamic({str(dynamic).lower()})')
            client.pump(3)
            start_frame = fixture.evaluate('window.benchmark.frames')
            previous_pixels, paints = canvas_sample(client), 0
            start = process_stats(desktop.pid)
            start_bytes, start_messages = received['bytes'], received['messages']
            begin = time.monotonic()
            peak, remote_peak = start['pss'], start['remote_pss']
            while time.monotonic()-begin < duration:
                client.pump(.5)
                sample = process_stats(desktop.pid)
                peak, remote_peak = max(peak,sample['pss']), max(remote_peak,sample['remote_pss'])
                pixels = canvas_sample(client)
                if pixels != previous_pixels:
                    paints += 1
                previous_pixels = pixels
            elapsed = time.monotonic()-begin
            end = process_stats(desktop.pid)
            frames = fixture.evaluate('window.benchmark.frames')-start_frame
            scale = 100/os.sysconf('SC_CLK_TCK')/elapsed
            measurement = dict(phase=phase, seconds=round(elapsed,2),
                desktop_cpu_percent_one_core=round((end['ticks']-start['ticks'])*scale,2),
                remote_backend_cpu_percent_one_core=round((end['remote_ticks']-start['remote_ticks'])*scale,2),
                desktop_peak_pss_mib=round(peak/2**20,2), remote_backend_peak_pss_mib=round(remote_peak/2**20,2),
                websocket_payload_kib_per_second=round((received['bytes']-start_bytes)/elapsed/1024,2),
                websocket_messages_per_second=round((received['messages']-start_messages)/elapsed,2),
                fixture_frames_per_second=round(frames/elapsed,2), client_observed_changes_per_second=round(paints/elapsed,2))
            print(f'METRIC {backend}: {json.dumps(measurement)}', flush=True)
            if dynamic:
                assert frames/elapsed >= 10, 'Fixture stalled'
                assert received['bytes'] > start_bytes+1024, 'Client received no animation updates'
                assert paints/elapsed >= .5, 'Remote canvas animation stalled'
                assert connected(), 'Remote client disconnected during benchmark'
            measurements.append(measurement)
        assert healthy(), 'Desktop unhealthy after resize and benchmark'
        assert Path('/run/115').stat().st_mode & 0o777 == 0o700
        assert Path('/usr/share/backgrounds/115/desktop.png').stat().st_size > 0
        assert not re.search(r'XDG_RUNTIME_DIR.*not available|Unable to set up transient service directory', (OUT/f'{backend}-desktop.log').read_text())
        return dict(backend=backend, resolutions=sizes, measurements=measurements, client_js_errors=received['errors'])
    except Exception:
        if client:
            try:
                diagnostic = client.evaluate("({url:location.href,visibility:document.visibilityState,paint:window.remotePaint,canvas:Array.from(document.querySelectorAll('canvas')).map(c=>[c.width,c.height]),body:document.body?.innerText.slice(0,1200)})")
                diagnostic['events'] = locals().get('received',{})
                (OUT/f'{backend}-client-diagnostic.json').write_text(json.dumps(diagnostic,indent=2))
                print(f'Client failure diagnostic saved: {backend}-client-diagnostic.json',flush=True)
            except Exception as error:
                print(f'Unable to capture client diagnostic: {error}',flush=True)
        raise
    finally:
        for remote in (client,fixture):
            if remote:
                remote.close()
        stop_group(client_process)
        stop_group(desktop)
        log.close()
        client_log.close()
        if desktop and desktop.returncode not in (0,-15):
            print((OUT/f'{backend}-desktop.log').read_text(), flush=True)
        shutil.rmtree(profile, ignore_errors=True)


def main():
    OUT.mkdir(exist_ok=True)
    for backend in ('kasm','legacy'):
        result = subprocess.run(['/opt/115/entrypoint.sh'], env={**os.environ,'REMOTE_BACKEND':backend,'PASSWORD':'invalid'}, capture_output=True, text=True, timeout=5)
        assert result.returncode != 0 and 'PASSWORD must' in result.stderr
        assert 'dbus' not in result.stderr, 'Password validated after starting D-Bus'
    report = dict(scene='vendored local fixture, 1600x900, target 30 FPS, serial fresh profiles',
                  machine=os.uname().machine, available_cpu_count=len(os.sched_getaffinity(0)),
                  cpu_unit='100% = one core; server process tree excludes headless web client',
                  transfer_unit='received WebSocket payload; excludes HTTP, TLS and TCP overhead',
                  pixel_sample_unit='observed pixel changes at approximately 2 Hz; not rendered FPS',
                  chromium_shared_memory='CI only: --disable-dev-shm-usage for 64 MiB executor containers; production Compose uses 1 GiB shm',
                  scope='synthetic scene on this executor; not a guarantee for 115 video or WAN performance', backends=[])
    for backend in os.environ.get('CI_BACKENDS', 'legacy,kasm').split(','):
        assert backend in ('legacy','kasm')
        report['backends'].append(run_backend(backend))
        (OUT/'performance.json').write_text(json.dumps(report,indent=2)+'\n')
    with (OUT/'performance.csv').open('w',newline='') as stream:
        fields = ['backend']+list(report['backends'][0]['measurements'][0])
        writer = csv.DictWriter(stream,fieldnames=fields)
        writer.writeheader()
        for backend in report['backends']:
            for measurement in backend['measurements']:
                writer.writerow({'backend':backend['backend'],**measurement})
    names = ', '.join(backend['backend'] for backend in report['backends'])
    print(f'PASS: verified backends [{names}], authenticated web clients, actual remote resize and performance samples', flush=True)


if __name__ == '__main__':
    main()
