import sys, pathlib
import pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fixtures


@pytest.fixture(scope="session")
def book_pdf(tmp_path_factory):
    return fixtures.two_column_book(tmp_path_factory.mktemp("pdf") / "book.pdf")


@pytest.fixture()
def project(tmp_path, book_pdf):
    from mdkb.project import Project
    return Project.create(tmp_path / "proj", book_pdf)
