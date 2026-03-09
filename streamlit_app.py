import streamlit as st
import sys
import os
import io
import traceback
import threading
import time
import uuid
from markdown import markdown as md_to_html

try:
    import pdfkit
    _HAS_PDFKIT = True
except Exception:
    _HAS_PDFKIT = False

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    _HAS_REPORTLAB = True
except Exception:
    _HAS_REPORTLAB = False

# Ensure project 'src' is importable (mirrors main.py behavior)
ROOT = os.path.dirname(__file__)
SRC_DIR = os.path.join(ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, ROOT)
    # also add src and src/config so modules like `agents` and `tasks` can be imported
    if SRC_DIR not in sys.path:
        sys.path.insert(0, SRC_DIR)
    CONFIG_DIR = os.path.join(SRC_DIR, "config")
    if CONFIG_DIR not in sys.path:
        sys.path.insert(0, CONFIG_DIR)
 
from src.crew import crew

# Background results store (thread-safe)
_BG_RESULTS = {}
_BG_LOCK = threading.Lock()

st.set_page_config(page_title="Crew AI — Visualizador", layout="wide")
st.title("Crew AI — Visualizador de Agentes")

topic = st.text_input("Tema da pesquisa", value="")
run = st.button("Executar Crew")

if 'outputs' not in st.session_state:
    st.session_state['outputs'] = {}
    st.session_state['final'] = None
if 'worker_thread' not in st.session_state:
    st.session_state['worker_thread'] = None
if 'agent_names' not in st.session_state:
    st.session_state['agent_names'] = []
if 'agent_progress' not in st.session_state:
    st.session_state['agent_progress'] = {}
if 'agent_status' not in st.session_state:
    st.session_state['agent_status'] = {}

def _make_pdf_from_html(html: str) -> bytes:
    # Prefer pdfkit (wkhtmltopdf) when available
    if _HAS_PDFKIT:
        try:
            return pdfkit.from_string(html, False)
        except Exception:
            pass

    # Fallback to a very small ReportLab renderer (plain text)
    if _HAS_REPORTLAB:
        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=letter)
        textobj = c.beginText(40, 750)
        # Strip tags crudely
        import re
        plain = re.sub(r'<[^>]+>', '', html)
        for line in plain.splitlines():
            textobj.textLine(line[:120])
        c.drawText(textobj)
        c.showPage()
        c.save()
        buffer.seek(0)
        return buffer.read()

    raise RuntimeError('Nenhum conversor de PDF disponível (instale pdfkit/wkhtmltopdf ou reportlab)')

def _run_crew(topic: str):
    try:
        result = crew.kickoff(inputs={"topic": topic})
        return result
    except Exception as e:
        return f"ERRO: {e}\n{traceback.format_exc()}"

def _background_worker(topic: str, agent_names: list, run_id: str):
    # Background thread must NOT call Streamlit APIs (no st.* here)
    try:
        outputs = {}
        progress = {n: 0 for n in agent_names}
        status = {n: 'pending' for n in agent_names}

        agent_objs = getattr(crew, 'agents', None)
        if not agent_objs:
            # fallback to executing the whole crew at once
            for n in agent_names:
                status[n] = 'running'
                with _BG_LOCK:
                    _BG_RESULTS[run_id] = {'final': None, 'outputs': outputs, 'progress': progress, 'status': status, 'error': None}
            try:
                result = crew.kickoff(inputs={"topic": topic})
                for n in agent_names:
                    outputs[n] = str(result)
                    progress[n] = 100
                    status[n] = 'done'
                with _BG_LOCK:
                    _BG_RESULTS[run_id] = {'final': result, 'outputs': outputs, 'progress': progress, 'status': status, 'error': None}
                return
            except Exception as e:
                err = f"{e}\n{traceback.format_exc()}"
                for n in agent_names:
                    status[n] = 'error'
                with _BG_LOCK:
                    _BG_RESULTS[run_id] = {'final': None, 'outputs': outputs, 'progress': progress, 'status': status, 'error': err}
                return

        # iterate agents if available
        for a in agent_objs:
            name = getattr(a, 'role', None) or getattr(a, 'name', None) or str(a)
            if name not in agent_names:
                agent_names.append(name)
                progress[name] = 0
                status[name] = 'pending'

            status[name] = 'running'
            progress[name] = 0
            with _BG_LOCK:
                _BG_RESULTS[run_id] = {'final': None, 'outputs': outputs, 'progress': progress, 'status': status, 'error': None}

            # pick executor
            executor = None
            for attr in ('run', 'execute', 'kickoff', 'call', '__call__'):
                fn = getattr(a, attr, None)
                if callable(fn):
                    executor = fn
                    break

            if executor is None:
                outputs[name] = 'SKIPPED: no callable method found on agent'
                status[name] = 'skipped'
                progress[name] = 100
                with _BG_LOCK:
                    _BG_RESULTS[run_id] = {'final': None, 'outputs': outputs, 'progress': progress, 'status': status, 'error': None}
                continue

            result_container = {'result': None, 'error': None}

            def _call_agent():
                import sys as _sys, io as _io
                out_buf = _io.StringIO()
                err_buf = _io.StringIO()
                old_out, old_err = _sys.stdout, _sys.stderr
                _sys.stdout, _sys.stderr = out_buf, err_buf
                try:
                    try:
                        r = executor(inputs={"topic": topic})
                    except TypeError:
                        r = executor({"topic": topic})
                    result_container['result'] = r
                except Exception as e:
                    result_container['error'] = f"{e}\n{traceback.format_exc()}"
                finally:
                    # capture final stdout/stderr
                    result_container['out'] = out_buf.getvalue()
                    result_container['err'] = err_buf.getvalue()
                    _sys.stdout, _sys.stderr = old_out, old_err

            t = threading.Thread(target=_call_agent, daemon=True)
            t.start()

            # animate progress while thread runs and write interim states to the BG store
            p = 0
            last_logged = ""
            while t.is_alive():
                p = min(95, p + 5)
                progress[name] = p
                # read any interim stdout from the worker
                out_text = result_container.get('out', '') or ''
                err_text = result_container.get('err', '') or ''
                combined_logs = out_text + ("\nERR:\n" + err_text if err_text else "")
                if combined_logs != last_logged:
                    # store logs in outputs as well as in a dedicated logs map
                    outputs.setdefault(name, "")
                    outputs[name] = combined_logs
                    last_logged = combined_logs

                with _BG_LOCK:
                    _BG_RESULTS[run_id] = {'final': None, 'outputs': outputs, 'progress': progress, 'status': status, 'error': None}
                time.sleep(0.2)

            # thread finished — capture final logs/results
            out_text = result_container.get('out', '') or ''
            err_text = result_container.get('err', '') or ''
            if result_container.get('error'):
                status[name] = 'error'
                progress[name] = 100
                outputs[name] = result_container['error'] + "\n" + out_text + ("\nERR:\n" + err_text if err_text else "")
            else:
                status[name] = 'done'
                progress[name] = 100
                outputs[name] = (str(result_container.get('result')) if result_container.get('result') is not None else '') + "\n" + out_text + ("\nERR:\n" + err_text if err_text else "")

            with _BG_LOCK:
                _BG_RESULTS[run_id] = {'final': None, 'outputs': outputs, 'progress': progress, 'status': status, 'error': None}

        # all agents done
        final_combined = outputs.copy()
        with _BG_LOCK:
            _BG_RESULTS[run_id] = {'final': final_combined, 'outputs': outputs, 'progress': progress, 'status': status, 'error': None}
    except Exception as e:
        err = f"{e}\n{traceback.format_exc()}"
        with _BG_LOCK:
            _BG_RESULTS[run_id] = {'final': None, 'outputs': {}, 'progress': {}, 'status': {}, 'error': err}

if run:
    if not topic:
        st.warning("Por favor, informe um tema antes de executar.")
    else:
        # prepare agent names from crew if available
        try:
            agent_objs = getattr(crew, 'agents', None)
            if agent_objs:
                names = []
                for a in agent_objs:
                    name = getattr(a, 'role', None) or getattr(a, 'name', None) or str(a)
                    names.append(name)
                st.session_state['agent_names'] = names
            else:
                # fallback to three default agents from config
                st.session_state['agent_names'] = ['Research Specialist', 'Data Analyst', 'Content Writer']
        except Exception:
            st.session_state['agent_names'] = ['Research Specialist', 'Data Analyst', 'Content Writer']

        # initialize outputs placeholder per-agent
        st.session_state['outputs'] = {n: 'pending' for n in st.session_state['agent_names']}
        st.session_state['agent_progress'] = {n: 0 for n in st.session_state['agent_names']}
        st.session_state['agent_status'] = {n: 'pending' for n in st.session_state['agent_names']}

        # start background thread to run the crew kickoff with a run_id
        if not st.session_state.get('worker_thread'):
            run_id = str(uuid.uuid4())
            st.session_state['current_run_id'] = run_id
            # initialize BG_RESULTS entry
            with _BG_LOCK:
                _BG_RESULTS[run_id] = {'final': None, 'outputs': {}, 'progress': st.session_state['agent_progress'], 'status': st.session_state['agent_status'], 'error': None}
            worker = threading.Thread(target=_background_worker, args=(topic, st.session_state['agent_names'], run_id), daemon=True)
            st.session_state['worker_thread'] = worker
            worker.start()

        # Show a non-blocking spinner while thread runs
        st.success("Execução iniciada — aguarde os agentes terminarem.")

# Display outputs and live progress
run_id = st.session_state.get('current_run_id')
worker = st.session_state.get('worker_thread')

# If a worker exists but is finished, clear worker reference
if worker is not None:
    try:
        if not worker.is_alive():
            st.session_state['worker_thread'] = None
    except Exception:
        st.session_state['worker_thread'] = None

# Pull latest BG results if available
if run_id:
    with _BG_LOCK:
        data = _BG_RESULTS.get(run_id, {})
    if data:
        # update session_state from BG data (main thread only)
        st.session_state['outputs'] = data.get('outputs', st.session_state.get('outputs', {}))
        st.session_state['agent_progress'] = data.get('progress', st.session_state.get('agent_progress', {}))
        st.session_state['agent_status'] = data.get('status', st.session_state.get('agent_status', {}))
        if data.get('final') is not None:
            st.session_state['final'] = data.get('final')
        if data.get('error'):
            st.error(data.get('error'))

# Only show UI when there's either a running job or a final result
if st.session_state.get('worker_thread') is not None or st.session_state.get('final') is not None:
    st.subheader("Saída dos agentes (renderizada como Markdown)")
    left, right = st.columns([3,1])

    with left:
        agent_names = st.session_state.get('agent_names', list(st.session_state.get('outputs', {}).keys()))
        placeholders = []
        for name in agent_names:
            placeholders.append(st.container())

        # render current progress/status for each agent
        for idx, name in enumerate(agent_names):
            with placeholders[idx]:
                status = st.session_state['agent_status'].get(name, 'pending')
                progress = st.session_state['agent_progress'].get(name, 0)
                if status == 'running':
                    st.markdown(f"**{name}** — running")
                    st.progress(progress)
                    st.write(f"Status: {status}")
                    show_logs = st.checkbox("Mostrar logs", key=f"show_logs_{run_id}_{name}")
                    if show_logs:
                        logs = st.session_state['outputs'].get(name, '')
                        st.text_area(f"Logs — {name}", value=logs, height=200, key=f"logs_area_{run_id}_{name}")
                elif status == 'pending':
                    st.markdown(f"**{name}** — pending")
                    st.progress(progress)
                    show_logs = st.checkbox("Mostrar logs", key=f"show_logs_{run_id}_{name}")
                    if show_logs:
                        logs = st.session_state['outputs'].get(name, '')
                        st.text_area(f"Logs — {name}", value=logs, height=200, key=f"logs_area_{run_id}_{name}")
                elif status == 'done':
                    st.markdown(f"**{name}** — done")
                    st.progress(100)
                    content = st.session_state['outputs'].get(name, '')
                    try:
                        if not isinstance(content, str):
                            content = str(content)
                        st.markdown(content, unsafe_allow_html=True)
                    except Exception:
                        st.write(content)
                    show_logs = st.checkbox("Mostrar logs", key=f"show_logs_{run_id}_{name}")
                    if show_logs:
                        logs = st.session_state['outputs'].get(name, '')
                        st.text_area(f"Logs — {name}", value=logs, height=200, key=f"logs_area_{run_id}_{name}")
                elif status == 'error':
                    st.markdown(f"**{name}** — error", unsafe_allow_html=True)
                    st.error(st.session_state['outputs'].get(name, 'Erro sem detalhes'))
                    st.progress(100)
                    show_logs = st.checkbox("Mostrar logs", key=f"show_logs_{run_id}_{name}")
                    if show_logs:
                        logs = st.session_state['outputs'].get(name, '')
                        st.text_area(f"Logs — {name}", value=logs, height=200, key=f"logs_area_{run_id}_{name}")
                elif status == 'skipped':
                    st.markdown(f"**{name}** — skipped")
                    st.progress(100)
                    show_logs = st.checkbox("Mostrar logs", key=f"show_logs_{run_id}_{name}")
                    if show_logs:
                        logs = st.session_state['outputs'].get(name, '')
                        st.text_area(f"Logs — {name}", value=logs, height=200, key=f"logs_area_{run_id}_{name}")

    with right:
        st.markdown("**Relatório**")
        # Compose a single markdown doc
        combined = "\n\n".join([f"## {k}\n\n{v}" for k,v in st.session_state.get('outputs', {}).items()])
        st.download_button("Baixar .md", combined, file_name="crew_report.md", mime="text/markdown")

        # Try to generate a PDF and provide a download
        try:
            html = md_to_html(combined)
            pdf_bytes = _make_pdf_from_html(html)
            st.download_button("Baixar PDF", pdf_bytes, file_name="crew_report.pdf", mime="application/pdf")
        except Exception as e:
            st.warning("Geração de PDF indisponível: instale `pdfkit` + `wkhtmltopdf` oder `reportlab`.")
            st.write(str(e))

        st.markdown("---")
        st.markdown("*Dica:* se faltar conversor de PDF, instale `wkhtmltopdf` ou `reportlab`.*")
