"""Authenticated private UI. Runtime construction and worker start are explicit.

skuld.app.local is the only launch path; tests and local previews use
create_app(runtime, start_worker=False). Importing this module creates neither a user nor a worker.
"""
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
import threading
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from skuld.app.onboarding import OnboardingService, parse_export
MAX_REQUEST_BYTES = 11 * 1024 * 1024  # HTTP body guard, unchanged; files stream through Runtime.import_file (D9)
from skuld.app.runtime import ProviderFailure


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')
class SubjectInput(Input): name: str; simulation: bool = True
class ChatInput(Input): text: str; idempotency_key: str; session_id: str
class SourceInput(Input): url: str; name: str; poll_seconds: int = 60; adapter: str = 'json_facts'
class ModelInput(Input): route: str
class ReconstructionInput(Input): effective_at: str; known_at: str
class ApprovalInput(Input): action_hash: str
class ExecutionInput(Input): approval_id: str | None = None
class RevocationInput(Input): approval_id: str
class ProductFeedbackInput(Input):
    decision_id: str
    helpful: StrictBool | None = None
    text: str = ''
    idempotency_key: str = Field(min_length=1, max_length=200)
class OnboardingInput(Input):
    answers: dict[str, str]
    idempotency_key: str = Field(min_length=1, max_length=200)
    processing_consent: dict[str, Any] | None = None
class ImportPreviewInput(Input): payload: Any
class ImportCommitInput(Input):
    payload: Any
    preview_digest: str = Field(min_length=1, max_length=100)
    conversation_ids: list[str]
    local_storage_consent: bool = False


def create_app(runtime, *, start_worker=True, close_runtime=True):
    """Attach the real Runtime instance; no fake provider or shadow user state."""
    config = runtime.config
    onboarding = OnboardingService(runtime)

    @asynccontextmanager
    async def lifespan(app):
        thread = None
        if start_worker:
            thread = threading.Thread(target=runtime.worker, name='skuld-continuous-worker', daemon=True)
            thread.start()
        yield
        if thread:
            runtime.stop.set(); thread.join(timeout=15)
        if close_runtime:
            if not thread or not thread.is_alive(): runtime.close()

    app = FastAPI(title='Skuld · 未竟之事', lifespan=lifespan)
    app.state.runtime = runtime
    app.mount('/ui', StaticFiles(directory=Path(__file__).with_name('ui'), check_dir=False), name='ui')

    def token(req):
        header = req.headers.get('authorization', '')
        if not header.startswith('Bearer '): raise HTTPException(401, 'token required')
        return header[7:]

    def auth(u, req):
        try: runtime.authenticate(u, token(req))
        except (PermissionError, KeyError): raise HTTPException(403, 'subject access denied')

    def run(fn, *args):
        try: return fn(*args)
        except ProviderFailure as exc: raise HTTPException(503, str(exc))
        except (ValueError, KeyError) as exc: raise HTTPException(409, str(exc))

    @app.middleware('http')
    async def private_surface_headers(request, call_next):
        try: length = int(request.headers.get('content-length', '0'))
        except ValueError: return JSONResponse({'detail': 'Invalid content length'}, status_code=400)
        if length > MAX_REQUEST_BYTES:
            return JSONResponse({'detail': '文件过大，请缩小到 10 MiB 以内的相关对话。'}, status_code=413)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        if request.url.path == '/' or request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        if request.url.path == '/':
            response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        return response

    @app.get('/health')
    def health():
        return {'service': runtime.VERSION, 'simulation_only': config.get('simulation_only', True),
                'worker_last_tick': runtime.last_tick, 'worker_error': runtime.worker_error,
                'worker_enabled': start_worker, 'real_user_gate': 'CLOSED' if config.get('simulation_only', True) else 'PRIVATE',
                'local_onboarding_available': True}

    @app.get('/', response_class=HTMLResponse)
    def index(): return Path(__file__).with_name('index.html').read_text(encoding='utf-8')

    @app.post('/api/v1/subjects')
    def provision(body: SubjectInput, request: Request):
        import hmac
        if not hmac.compare_digest(token(request), config['gateway_key']): raise HTTPException(403, 'provisioning token required')
        return run(runtime.provision, body.name, body.simulation)

    @app.get('/api/v1/subjects/{u}/onboarding')
    def onboarding_status(u: str, request: Request): auth(u, request); return run(onboarding.status, u)

    @app.post('/api/v1/subjects/{u}/onboarding')
    def onboarding_commit(u: str, body: OnboardingInput, request: Request):
        auth(u, request); return run(onboarding.commit, u, body.answers, body.idempotency_key, body.processing_consent)

    @app.post('/api/v1/subjects/{u}/imports/preview')
    def import_preview(u: str, body: ImportPreviewInput, request: Request): auth(u, request); return run(parse_export, body.payload)

    @app.post('/api/v1/subjects/{u}/imports')
    def import_commit(u: str, body: ImportCommitInput, request: Request):
        auth(u, request); return run(onboarding.import_history, u, body.payload, body.preview_digest, body.conversation_ids, body.local_storage_consent)

    @app.get('/api/v1/subjects/{u}/product')
    def product_state(u: str, request: Request):
        auth(u, request)
        return run(lambda: {'state': runtime.product.reconstruct(u).to_dict(),
                            'work': runtime.state(u).get('product_work', []),
                            'capability_status': runtime.state(u).get('capability_status', {})})

    @app.post('/api/v1/subjects/{u}/product/feedback')
    def product_feedback(u: str, body: ProductFeedbackInput, request: Request):
        auth(u, request)
        with runtime.lock:
            return run(runtime.product.world.feedback, u, body.decision_id, body.helpful, body.text, body.idempotency_key)

    @app.post('/api/v1/subjects/{u}/chat')
    def chat(u: str, body: ChatInput, request: Request): auth(u, request); return run(runtime.chat, u, body.text, body.idempotency_key, body.session_id)
    @app.post('/api/v1/subjects/{u}/sources')
    def source(u: str, body: SourceInput, request: Request): auth(u, request); return run(runtime.add_source, u, body.url, body.name, body.poll_seconds, body.adapter)
    @app.post('/api/v1/subjects/{u}/model')
    def model(u: str, body: ModelInput, request: Request): auth(u, request); return run(runtime.switch, u, body.route)
    @app.post('/api/v1/subjects/{u}/reconstruct')
    def reconstruct(u: str, body: ReconstructionInput, request: Request):
        auth(u, request)
        current = run(runtime.product.reconstruct, u, body.effective_at, body.known_at).to_dict()
        # Keep the former envelope for consumers while using one canonical reducer.
        current['nodes'] = [node for group in ('facts', 'goals', 'closed_goals', 'preferences', 'relationships', 'boundaries', 'hypotheses', 'episodes', 'predictions') for node in current.get(group, [])]
        return current
    @app.get('/api/v1/subjects/{u}/state')
    def state(u: str, request: Request):
        auth(u, request)
        result = run(runtime.state, u)
        result['worker_enabled'] = start_worker
        result['owner_name'] = runtime.meta(u)['name']
        result['model_options'] = [{'route': route, 'label': str(cfg.get('model') or route),
                                    'configured': bool(cfg.get('key') and cfg.get('base_url') and cfg.get('model'))}
                                   for route, cfg in config.get('routes', {}).items()]
        return result
    @app.get('/api/v1/subjects/{u}/export')
    def export(u: str, request: Request): auth(u, request); return run(runtime.export, u)
    @app.get('/api/v1/subjects/{u}/artifacts/{aid}', response_class=PlainTextResponse)
    def artifact(u: str, aid: str, request: Request): auth(u, request); return run(runtime.artifact, u, aid)
    @app.post('/api/v1/subjects/{u}/actions/{aid}/approve')
    def approve(u: str, aid: str, body: ApprovalInput, request: Request): auth(u, request); return run(runtime.approve, u, aid, body.action_hash)
    @app.post('/api/v1/subjects/{u}/actions/{aid}/execute')
    def execute(u: str, aid: str, body: ExecutionInput, request: Request): auth(u, request); return run(runtime.execute, u, aid, body.approval_id)
    @app.post('/api/v1/subjects/{u}/actions/{aid}/revoke')
    def revoke(u: str, aid: str, body: RevocationInput, request: Request): auth(u, request); return run(runtime.revoke, u, aid, body.approval_id)
    return app

