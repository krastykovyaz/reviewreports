from mmengine.config import read_base
with read_base():
    from .base import memory_config, window_size, max_tokens
    from .agents.reviewreports import reviewreports_agent
    from .tools.website_audit import website_audit_tool
    from .tools.code_review import code_review_tool
    from .tools.document_review import document_review_tool
    from .tools.report import report_tool
    from .tools.browser import browser_tool
    from .tools.mdify import mdify_tool
    from .memory.general_memory_system import memory_system as general_memory_system

tag = "reviewreports"
workdir = f"workdir/{tag}"
log_path = "agent.log"

version = "1.0.0"
model_name = "ollama/qwen3-30b"
concurrency = 4

env_names = []
memory_names = [
    "general_memory_system"
]
agent_names = [
    "reviewreports_agent"
]
tool_names = [
    'bash',
    'mdify',
    'python_interpreter',
    'done',
    'todo',
    'website_audit',
    'code_review',
    'document_review',
    'report',
    'browser',
]

#-----------------WEBSITE AUDIT TOOL CONFIG-----------------
website_audit_tool.update(
    base_dir=f"{workdir}/tool/website_audit",
    model_name=model_name,
)
#-----------------CODE REVIEW TOOL CONFIG-----------------
code_review_tool.update(
    base_dir=f"{workdir}/tool/code_review",
    model_name=model_name,
)
#-----------------DOCUMENT REVIEW TOOL CONFIG-----------------
document_review_tool.update(
    base_dir=f"{workdir}/tool/document_review",
    model_name=model_name,
)
#-----------------BROWSER TOOL CONFIG-----------------
browser_tool.update(
    model_name="openrouter/gpt-4.1",
    base_dir=f"{workdir}/tool/browser",
)
#-----------------REPORT TOOL CONFIG-----------------
report_tool.update(
    model_name=model_name,
    base_dir=f"{workdir}/tool/report",
)
#-----------------MDIFY TOOL CONFIG-----------------
mdify_tool.update(
    base_dir=f"{workdir}/tool/mdify",
)
#-----------------GENERAL MEMORY SYSTEM CONFIG-----------------
general_memory_system.update(
    base_dir=f"{workdir}/memory/general_memory_system",
    model_name=model_name,
    max_summaries=10,
    max_insights=10,
)
#-----------------REVIEWREPORTS AGENT CONFIG-----------------
reviewreports_agent.update(
    workdir=workdir,
    model_name=model_name,
    memory_name=memory_names[0]
)
