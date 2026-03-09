from crewai import Task
from agents import research_agent, analysis_agent, writer_agent

research_task = Task(
    description="Pesquise informações relevantes sobre {topic}",
    agent=research_agent,
    expected_output="Lista estruturada de fatos e insights sobre o tema"
)

analysis_task = Task(
    description="Analise as informações coletadas e organize em tópicos claros",
    agent=analysis_agent,
    expected_output="Resumo analítico estruturado"
)

writing_task = Task(
    description="Crie um relatório final didático baseado na análise",
    agent=writer_agent,
    expected_output="Relatório final com introdução, tópicos e conclusão"
)