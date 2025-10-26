import json
from titlecase import titlecase
import re
from ollama import Client

client = Client(host="http://localhost:11434")  # Change port if needed


def ask_gemma(prompt):
    """
    Ask Gemma to extract article title and authors, enforcing title case.
    """

    system_prompt = (
        "You are a precise JSON extractor. "
        "Return ONLY valid JSON with this structure:\n"
        "{\n"
        '  "title": "<title>",\n'
        '  "author": ["<author1>", "<author2>", ...]\n'
        "}\n\n"
        "Rules:\n"
        "- Title must be in title case (e.g., 'The Cat in the Hat').\n"
        "- Lowercase articles/conjunctions/prepositions unless first word.\n"
        "- Do NOT output all-uppercase or all-lowercase titles.\n"
        "- If no author is found, return an empty list [].\n"
        "- Only use data from the user text.\n"
        "- No markdown, no explanations — JSON only."
    )

    response = client.chat(
        model="gemma2:2b",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
    )

    content = response["message"]["content"].strip()

    # Attempt to parse JSON safely
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        # Extract JSON portion if surrounded by text or formatting
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                data = {"title": "", "author": []}
        else:
            data = {"title": "", "author": []}

    # Normalize title casing if model ignored instruction
    if "title" in data and data["title"].isupper():
        data["title"] = titlecase(data["title"].lower())

    # Ensure author list exists
    if "author" not in data or not isinstance(data["author"], list):
        data["author"] = []

    return json.dumps(data, ensure_ascii=False, indent=4)
# Example usage
if __name__ == "__main__":
    while True:
        question = input("\nYour question (type 'quit' to exit): ")
        if question.lower() == "quit":
            break

        answer = ask_gemma(question)
        print(f"\nGemma: {answer}")
