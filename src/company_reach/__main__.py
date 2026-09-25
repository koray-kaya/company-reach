"""`python -m company_reach`: the same commands as `company-reach`.

The front page starts its commands this way, with the interpreter that runs
the page, so a button and the terminal run the same code.
"""

from company_reach.cli import app

app(prog_name="company-reach")
