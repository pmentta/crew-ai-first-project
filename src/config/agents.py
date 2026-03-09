from crewai import Agent
from llm_config import llm

research_agent = Agent(
    role="Research Specialist",
    goal="Encontrar informações relevantes e confiáveis sobre um tópico",
    backstory="Especialista em pesquisa e análise de informações",
    llm=llm,
    verbose=True
)

analysis_agent = Agent(
    role="Data Analyst",
    goal="Analisar e estruturar as informações coletadas",
    backstory="Analista experiente em sintetizar grandes volumes de informação",
    llm=llm,
    verbose=True
)

writer_agent = Agent(
    role="Content Writer",
    goal="Escrever um relatório claro e didático",
    backstory="Redator especializado em transformar dados complexos em conteúdo simples",
    llm=llm,
    verbose=True
)