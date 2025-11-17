# frontend/app.py
import os
import json
import requests
import streamlit as st
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

FASTAPI_URL = os.getenv("FASTAPI_URL", "http://127.0.0.1:8000").rstrip("/")
TOKEN = os.getenv("MCP_ACCESS_TOKEN", None)

HEADERS = {}
if TOKEN:
    HEADERS["x-mcp-token"] = TOKEN

st.set_page_config(page_title="Research Agent", layout="wide")

# Helper function to call endpoints
def post(path, payload, timeout=120):
    url = f"{FASTAPI_URL}{path}"
    r = requests.post(url, json=payload, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()

# UI layout
st.title("🔍 Automated Research Agent")
st.markdown("Enter a research topic and the LangGraph orchestrator will plan, search, extract, and synthesize findings.")

with st.form("topic_form"):
    topic_input = st.text_input("Research topic", value="Artificial Intelligence in Healthcare")
    max_results = st.number_input("Max search results per subtopic", min_value=1, max_value=10, value=3)
    submitted = st.form_submit_button("🚀 Start Research")

# Sidebar: live logs
st.sidebar.header("📋 Agent Activity")
log_container = st.sidebar.empty()
log_lines = []

def log(msg):
    ts = datetime.utcnow().isoformat(timespec='seconds')
    log_lines.append(f"{ts} | {msg}")
    log_container.text("\n".join(log_lines[-100:]))

# Layout for results
subtopic_col = st.container()
notes_col = st.container()
report_col = st.expander("📄 Final Synthesized Report", expanded=True)

def run_research_with_langgraph(topic, max_results):
    """Call the new /research/langgraph endpoint"""
    log(f"Starting LangGraph orchestration for: {topic}")
    
    try:
        result = post("/research/langgraph", {
            "topic": topic,
            "max_results": max_results
        })
        log("✅ LangGraph workflow completed successfully")
        return result
    except Exception as e:
        log(f"❌ LangGraph workflow failed: {e}")
        st.error(f"Research failed: {e}")
        return None

# Bind UI actions
if submitted:
    if not topic_input.strip():
        st.error("Please enter a topic.")
    else:
        with st.spinner("🔄 Running research agent (this may take 30-60 seconds)..."):
            try:
                result = run_research_with_langgraph(topic_input.strip(), max_results)
                
                if result and result.get("success"):
                    # Display subtopics
                    with subtopic_col:
                        st.header("📍 Identified Subtopics")
                        subtopics = result.get("subtopics", [])
                        if subtopics:
                            for i, subtopic in enumerate(subtopics, 1):
                                st.write(f"{i}. {subtopic}")
                        else:
                            st.info("No subtopics identified")
                    
                    # Display notes per subtopic
                    with notes_col:
                        st.header("📚 Research Notes")
                        notes_dict = result.get("notes", {})
                        
                        if notes_dict:
                            for subtopic, notes_list in notes_dict.items():
                                with st.expander(f"📌 {subtopic} ({len(notes_list)} sources)"):
                                    if notes_list:
                                        for note in notes_list:
                                            st.markdown(f"**{note.get('url', 'Unknown Source')}**")
                                            st.write(note.get('summary', 'No summary available'))
                                            st.divider()
                                    else:
                                        st.info("No notes found for this subtopic")
                        else:
                            st.info("No notes extracted")
                    
                    # Display final report
                    with report_col:
                        report = result.get("report", "")
                        if report:
                            st.markdown(report)
                        else:
                            st.info("No report generated")
                    
                    # Export button
                    md = f"""# Research Report: {topic_input}

Generated on: {datetime.utcnow().isoformat()}

## Subtopics
{chr(10).join([f"- {s}" for s in subtopics])}

## Research Notes
{chr(10).join([f"### {st}" + chr(10) + chr(10).join([f"- [{n.get('url', 'Source')}]({n.get('url', '#')})" + chr(10) + f"  {n.get('summary', '')}" for n in notes_dict.get(st, [])]) + chr(10) for st in subtopics])}

## Final Report
{report}
"""
                    
                    bname = f"research_report_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.md"
                    st.download_button("📥 Download Report (Markdown)", data=md, file_name=bname, mime="text/markdown")
                    
                    log("✅ Research completed and displayed")
                    st.success("✅ Research agent completed successfully!")
                else:
                    st.error("Research returned unexpected response")
                    log("❌ Unexpected response from research endpoint")
                    
            except Exception as e:
                st.error(f"❌ Error: {e}")
                log(f"❌ Error: {e}")
