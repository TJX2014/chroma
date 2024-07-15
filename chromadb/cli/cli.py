from typing import Optional

import rich.markdown
from rich.progress import Progress, SpinnerColumn, TextColumn
from typing_extensions import Annotated
import typer
import rich
import uvicorn
import os
import webbrowser

from chromadb.cli.utils import set_log_file_path
from chromadb.config import Settings, System
from chromadb.db.impl.sqlite import SqliteDB

app = typer.Typer()

_logo = """
                \033[38;5;069m(((((((((    \033[38;5;203m(((((\033[38;5;220m####
             \033[38;5;069m(((((((((((((\033[38;5;203m(((((((((\033[38;5;220m#########
           \033[38;5;069m(((((((((((((\033[38;5;203m(((((((((((\033[38;5;220m###########
         \033[38;5;069m((((((((((((((\033[38;5;203m((((((((((((\033[38;5;220m############
        \033[38;5;069m(((((((((((((\033[38;5;203m((((((((((((((\033[38;5;220m#############
        \033[38;5;069m(((((((((((((\033[38;5;203m((((((((((((((\033[38;5;220m#############
         \033[38;5;069m((((((((((((\033[38;5;203m(((((((((((((\033[38;5;220m##############
         \033[38;5;069m((((((((((((\033[38;5;203m((((((((((((\033[38;5;220m##############
           \033[38;5;069m((((((((((\033[38;5;203m(((((((((((\033[38;5;220m#############
             \033[38;5;069m((((((((\033[38;5;203m((((((((\033[38;5;220m##############
                \033[38;5;069m(((((\033[38;5;203m((((    \033[38;5;220m#########\033[0m

    """


@app.command()  # type: ignore
def run(
    path: str = typer.Option(
        "./chroma_data", help="The path to the file or directory."
    ),
    host: Annotated[
        Optional[str], typer.Option(help="The host to listen to. Default: localhost")
    ] = "localhost",
    log_path: Annotated[
        Optional[str], typer.Option(help="The path to the log file.")
    ] = "chroma.log",
    port: int = typer.Option(8000, help="The port to run the server on."),
    test: bool = typer.Option(False, help="Test mode.", show_envvar=False, hidden=True),
) -> None:
    """Run a chroma server"""

    print("\033[1m")  # Bold logo
    print(_logo)
    print("\033[1m")  # Bold
    print("Running Chroma")
    print("\033[0m")  # Reset

    typer.echo(f"\033[1mSaving data to\033[0m: \033[32m{path}\033[0m")
    typer.echo(
        f"\033[1mConnect to chroma at\033[0m: \033[32mhttp://{host}:{port}\033[0m"
    )
    typer.echo(
        "\033[1mGetting started guide\033[0m: https://docs.trychroma.com/getting-started\n\n"
    )

    # set ENV variable for PERSIST_DIRECTORY to path
    os.environ["IS_PERSISTENT"] = "True"
    os.environ["PERSIST_DIRECTORY"] = path
    os.environ["CHROMA_SERVER_NOFILE"] = "65535"
    os.environ["CHROMA_CLI"] = "True"

    # get the path where chromadb is installed
    chromadb_path = os.path.dirname(os.path.realpath(__file__))

    # this is the path of the CLI, we want to move up one directory
    chromadb_path = os.path.dirname(chromadb_path)
    log_config = set_log_file_path(f"{chromadb_path}/log_config.yml", f"{log_path}")
    config = {
        "app": "chromadb.app:app",
        "host": host,
        "port": port,
        "workers": 1,
        "log_config": log_config,  # Pass the modified log_config dictionary
        "timeout_keep_alive": 30,
    }

    if test:
        return

    uvicorn.run(**config)


@app.command()  # type: ignore
def vacuum(
    path: str = typer.Option(
        help="The path to a Chroma data directory.",
    ),
    force: bool = typer.Option(False, help="Force vacuuming without confirmation."),
) -> None:
    """Vacuum the database"""
    if not os.path.exists(path):
        rich.print(f"[bold red]Path {path} does not exist.[/bold red]")
        raise typer.Exit(code=1)

    if not force and not typer.confirm(
        "Are you sure you want to vacuum the database? This will block both reads and writes to the database, and may take a while."
    ):
        rich.print("Vacuum cancelled.")
        raise typer.Exit(code=0)

    # todo: check free tmp space?
    settings = Settings()
    settings.is_persistent = True
    settings.persist_directory = path
    system = System(settings=settings)
    sqlite = system.instance(SqliteDB)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        progress.add_task("Vacuuming (this may take a while)...")
        sqlite.vacuum()

    rich.print(":soap: [bold]vacuum complete![/bold]")


@app.command()  # type: ignore
def help() -> None:
    """Opens help url in your browser"""

    webbrowser.open("https://discord.gg/MMeYNTmh3x")


@app.command()  # type: ignore
def docs() -> None:
    """Opens docs url in your browser"""

    webbrowser.open("https://docs.trychroma.com")


if __name__ == "__main__":
    app()
