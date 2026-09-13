#!/usr/bin/env python3
"""Thin client for the local endpoint.

⚠️ Send NO sampling parameters. The server is launched with a matched profile
(`--temp 1.0 --top-p 0.95 --top-k 20 --presence-penalty 0.0 --reasoning-effort low`,
see QWEN38_LLAMACPP_SETUP.md) and opencode — which works well on this box — passes
nothing but the baseURL. Overriding one knob (e.g. temperature=0.2 against a top_p
tuned for 1.0) is the "carrying one across modes" trap the setup doc warns about:
it degraded output and sent the model into multi-thousand-token loops.
"""
import fcntl, json, os, pathlib, time, urllib.request

# Адрес и модель задаются окружением: конвейер не привязан ни к этой машине, ни к этой
# модели -- нужен лишь OpenAI-совместимый эндпоинт. Значения по умолчанию -- наши.
URL = os.environ.get('WW_LLM_URL', "http://10.99.99.1:18082/v1/chat/completions")
MODEL = os.environ.get('WW_LLM_MODEL', "qwen3.8-27b")
LOCK = pathlib.Path(__file__).resolve().parent.parent / '.endpoint.lock'


class Busy(RuntimeError):
    pass


class TooSlow(RuntimeError):
    """The model went into a long thinking spiral. Caller should split the work."""


def _acquire():
    """The server runs with -np 1: a second client does not run in parallel, it
    queues behind the first and looks exactly like a hang. Refuse instead."""
    fh = open(LOCK, 'w')
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        raise Busy(f'another client already holds {LOCK} -- the endpoint has one slot')
    fh.write(str(os.getpid()))
    fh.flush()
    return fh

def chat(system, user, max_tokens=16000, timeout=1800, on_delta=None, deadline=None):
    lock = _acquire()
    try:
        return _chat(system, user, max_tokens, timeout, on_delta, deadline)
    finally:
        lock.close()


def _chat(system, user, max_tokens, timeout, on_delta, deadline=None):
    stop_at = time.time() + deadline if deadline else None
    body = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": max_tokens,
    }
    if on_delta is None:
        req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)["choices"][0]["message"]["content"]

    body["stream"] = True
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    out, ntok, think = [], 0, 0
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode('utf-8', 'replace').strip()
            if not line.startswith('data:'):
                continue
            payload = line[5:].strip()
            if payload == '[DONE]':
                break
            try:
                d = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if stop_at and time.time() > stop_at:
                raise TooSlow(f'no answer within {deadline}s '
                              f'({think} thinking tokens, {ntok} content tokens)')
            for ch in d.get('choices', []):
                delta = ch.get('delta') or {}
                if delta.get('reasoning_content'):
                    think += 1
                    if think % 16 == 0:
                        on_delta(''.join(out) + f'\n[думает… {think} токенов]', ntok)
                    continue
                piece = delta.get('content')
                if piece:
                    out.append(piece)
                    ntok += 1
                    on_delta(''.join(out), ntok)
    return ''.join(out)
