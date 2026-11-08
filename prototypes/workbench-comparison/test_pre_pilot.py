import sys
from pathlib import Path
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pywebview_candidate import WorkbenchBridge

bridge=WorkbenchBridge(offline=True)
def settled():
    deadline = monotonic() + 10
    while monotonic() < deadline:
        for event in bridge.wait_events(100):
            if event['type'] == 'settled':
                return event
    raise AssertionError('settlement timeout')
try:
    for _ in range(2):
        request=bridge.submit('cancel test')
        assert request['accepted']
        assert bridge.cancel(request['request_id'])['accepted']
        result=settled()
        assert result['observation']['status']=='failed'
        assert result['observation']['reason_code']=='INTERACTION_CANCELLED'
    # Slow/disconnected consumer: one admitted request, bounded merged preview.
    for _ in range(10000):
        bridge._append_delta('stress', 'x' * 100)
    assert len(bridge._events)==1
    assert len(bridge._events[0]['text'])==64000
    assert bridge._events[0]['truncated']
    assert not bridge.submit('must reject until drained')['accepted']
    bridge.wait_events(0)
    request=bridge.submit('/fast /truncate')
    assert request['accepted']
    result=settled()
    content=bridge.read_report(result['reference']['token'])
    assert content['truncated'] and content['sha256'] is None
    print('PASS: repeated cancellation, canonical simulated reason/status, bounded preview, admission backpressure, metadata')
finally:
    bridge._session.close().result()
