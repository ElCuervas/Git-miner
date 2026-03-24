import os
import time
import json
import redis
from flask import Flask, render_template, Response, request, jsonify

app = Flask(__name__)

REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))
REDIS_KEY  = 'ranking_palabras'

def get_redis():
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/ranking')
def api_ranking():
    n = request.args.get('n', 20, type=int)
    n = max(1, min(n, 200))
    try:
        r = get_redis()
        raw = r.zrevrange(REDIS_KEY, 0, n - 1, withscores=True)
        total_unique = r.zcard(REDIS_KEY)
        data = [{'word': w, 'score': int(s)} for w, s in raw]
        return jsonify({'ranking': data, 'total_unique': total_unique, 'top_n': n})
    except Exception as e:
        return jsonify({'error': str(e), 'ranking': [], 'total_unique': 0}), 500


@app.route('/stream')
def stream():
    n = request.args.get('n', 20, type=int)
    n = max(1, min(n, 200))

    def event_generator():
        r = get_redis()
        last_payload = None
        while True:
            try:
                raw = r.zrevrange(REDIS_KEY, 0, n - 1, withscores=True)
                total_unique = r.zcard(REDIS_KEY)
                data = {
                    'ranking': [{'word': w, 'score': int(s)} for w, s in raw],
                    'total_unique': total_unique,
                    'top_n': n,
                    'ts': int(time.time())
                }
                payload = json.dumps(data)
                if payload != last_payload:
                    yield f"data: {payload}\n\n"
                    last_payload = payload
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
            time.sleep(1)

    return Response(event_generator(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
