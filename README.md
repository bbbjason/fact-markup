# fact-markup

GitHub Actions automation for the Codex `fact-markup` workflow. It fetches or reads an article, removes obvious boilerplate, normalizes the article to line-based text units, asks an OpenAI model to classify each unit as `FACT` or `NONFACT`, and writes two sibling outputs:

- `*.fact-marked.md`
- `*.judgments.jsonl`

## Setup

1. Open the repository settings.
2. Add an Actions secret named `OPENAI_API_KEY`.
3. Optional: add an Actions variable named `OPENAI_MODEL` if you want to override the default `gpt-5.4-mini`.

## Run

Go to **Actions** -> **Fact Markup** -> **Run workflow**.

You can provide either:

- `url`: fetch and process an article URL.
- `input_file`: process an existing Markdown file already committed in the repository.

`output_dir` defaults to `20_project/fact-markup` for fetched URLs. When `commit_outputs` is true, the workflow commits generated files back to the branch. The workflow also uploads generated files as a run artifact.

## Local usage

```bash
python -m pip install -r requirements-fact-markup.txt
export OPENAI_API_KEY=...
python scripts/fact_markup.py --url "https://example.com/article" --output-dir 20_project/fact-markup
```
