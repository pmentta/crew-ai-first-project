from crewai import Crew
from .config.agents import research_agent, analysis_agent, writer_agent
from .config.tasks import research_task, analysis_task, writing_task

crew = Crew(
    agents=[
        research_agent,
        analysis_agent,
        writer_agent
    ],
    tasks=[
        research_task,
        analysis_task,
        writing_task
    ],
    verbose=True
)