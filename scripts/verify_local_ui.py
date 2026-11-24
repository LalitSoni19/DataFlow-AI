"""Exercise the real UI and API against temporary synthetic data using Edge and Playwright."""
import argparse
import os, sys, time, socket, subprocess, tempfile, httpx, io
from contextlib import ExitStack
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dataflow_ai.pipeline import PipelineRunner
from dataflow_ai.settings import Settings

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--demo-dir", type=Path, help="Save the dashboard screenshot and animated walkthrough here.")
demo_dir = parser.parse_args().demo_dir
if demo_dir is not None:
    demo_dir = demo_dir.resolve()
    demo_dir.mkdir(parents=True, exist_ok=True)

def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1",0))
        return s.getsockname()[1]

with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
    stack.callback(os.chdir, os.getcwd())
    # Prevent developer or deployment credentials from reaching the fixture.
    for name in list(os.environ):
        if name.upper().startswith(("DATAFLOW_", "LLM_", "OLLAMA_", "MOTHERDUCK", "GEMINI_", "STREAMLIT_")):
            del os.environ[name]
    os.environ["PYTHONPATH"] = os.pathsep.join(map(str, (ROOT / "src", ROOT)))
    os.environ["DATAFLOW_ENV"] = "development"
    os.environ["DATAFLOW_DATA_DIR"] = tmp
    os.environ["DATAFLOW_PROJECT_ROOT"] = tmp
    os.chdir(tmp)
    sales = Path(tmp)/"sales.csv"
    sales.write_text("order_id,date,product,customer,currency,status,line_amount\nA,2026-03-01,Desk,North,USD,completed,200\nB,2026-03-02,Lamp,West,USD,paid,35\nC,2026-03-02,Lamp,West,USD,refunded,15\n")
    PipelineRunner(Settings.from_environment()).run_flexible_sales_files([sales])
    ui,api = port(),port()
    for args in [["uvicorn","dataflow_ai.api.main:app","--host","127.0.0.1","--port",str(api)],["streamlit","run",str(ROOT / "streamlit_app/app.py"),"--server.address","127.0.0.1","--server.port",str(ui),"--server.headless","true"]]:
        log=stack.enter_context(open(Path(tmp)/(args[0]+"-gate.log"),"w",encoding="utf8"))
        p=subprocess.Popen([sys.executable,"-m",*args],stdout=log,stderr=log)
        stack.callback(p.wait); stack.callback(p.terminate)
    for url in [f"http://127.0.0.1:{api}/health",f"http://127.0.0.1:{ui}/_stcore/health"]:
        for _ in range(70):
            try:
                if httpx.get(url).is_success: break
            except httpx.TransportError: pass
            time.sleep(0.4)
        else: raise RuntimeError(url)
    url=f"http://127.0.0.1:{api}/api"
    assert httpx.get(url+"/kpis/overview").json()["total_revenue"]==235
    assert "235.00" in httpx.get(url+"/reports/monthly?month=2026-03").text
    assert httpx.post(url+"/ask",json={"question":"What is total revenue?"},timeout=30).json()["rows"][0]["total_revenue"]==235
    print("FastAPI localhost verified",flush=True)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel="msedge",headless=True)
        page=browser.new_page(viewport={"width":1380,"height":920},device_scale_factor=1)
        page.set_default_timeout(25000)
        page.goto(f"http://127.0.0.1:{ui}")
        frames=[]
        def capture():
            if demo_dir is None:
                return
            frames.append(Image.open(io.BytesIO(page.screenshot())).convert("RGB").resize((966,644)))
        def navigate(title):
            page.get_by_role("link",name=title).click()
            page.get_by_role("heading",name=title,exact=True).wait_for()
            assert not page.get_by_test_id("stException").count()
        navigate("Import data")
        page.locator('input[type=file]').set_input_files(str(sales))
        page.get_by_role("button",name="Preview import").click()
        page.get_by_role("button",name="Publish reviewed files").wait_for()
        capture()
        page.get_by_role("button",name="Publish reviewed files").click()
        page.get_by_text("Published 3 accepted rows.",exact=True).wait_for()
        navigate("Sales overview")
        page.get_by_text("$235",exact=True).first.wait_for()
        if demo_dir is not None:
            page.screenshot(path=str(demo_dir/"sales-overview.png"))
        capture()
        navigate("Pipeline quality")
        navigate("Browse tables")
        navigate("SQL explorer")
        page.get_by_role("button",name="Run Query",exact=False).click()
        page.get_by_role("heading",name="Results",exact=True).wait_for()
        navigate("Monthly reports")
        page.get_by_role("button",name="Generate Report",exact=False).click()
        page.get_by_text("Download monthly report",exact=True).wait_for()
        capture()
        navigate("Ask your data")
        page.get_by_role("textbox",name="Question",exact=True).fill("What is total revenue?")
        page.get_by_role("button",name="Ask the warehouse").click()
        page.get_by_role("heading",name="Answer",exact=True).wait_for()
        assert "235" in page.locator("body").inner_text()
        page.get_by_role("heading",name="Answer",exact=True).scroll_into_view_if_needed()
        capture()
        if frames:
            frames[0].save(demo_dir/"sales-workflow.gif",save_all=True,append_images=frames[1:],duration=2200,loop=0)
        browser.close()
    print("All seven localhost routes verified.",flush=True)
