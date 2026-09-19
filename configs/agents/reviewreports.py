reviewreports_agent = dict(
    workdir = "workdir/reviewreports_agent",
    name = "reviewreports_agent",
    type = "Agent",
    description = "An agent that produces structured review reports for websites, code repositories, and documents (resumes, presentations, books).",
    model_name = "openrouter/gpt-4.1",
    prompt_name = "reviewreports",
    memory_name = "general_memory_system",
    max_tools = 10,
    max_steps = 15,
    review_steps = 5,
    log_max_length = 1000,
)
