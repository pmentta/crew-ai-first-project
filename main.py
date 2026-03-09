from src.crew import crew

def run():

    topic = input("Digite o tema da pesquisa: ")

    result = crew.kickoff(
        inputs={"topic": topic}
    )

    print("\nRESULTADO FINAL:\n")
    print(result)


if __name__ == "__main__":
    run()