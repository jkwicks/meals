import csv
import requests

# Fetch model metadata from OpenRouter API
response = requests.get("https://openrouter.ai/api/v1/models")
data = response.json().get("data", [])

# Select the top 50 models
top_50 = data[:50]

# Define CSV filename and headers
filename = "openrouter_top_50.csv"
headers = [
    "ID",
    "Name",
    "Context Length",
    "Prompt Price (per 1M)",
    "Completion Price (per 1M)",
]

# Write data to CSV file
with open(filename, mode="w", newline="", encoding="utf-8") as file:
    writer = csv.writer(file)
    writer.writerow(headers)

    for model in top_50:
        model_id = model.get("id", "")
        name = model.get("name", "")
        context_length = model.get("context_length", 0)

        pricing = model.get("pricing", {})
        # Convert prices to per-million token rates
        prompt_price = float(pricing.get("prompt", 0)) * 1_000_000
        completion_price = float(pricing.get("completion", 0)) * 1_000_000

        writer.writerow(
            [
                model_id,
                name,
                context_length,
                f"${prompt_price:.4f}",
                f"${completion_price:.4f}",
            ]
        )

print(
    f"Successfully exported {len(top_50)} models to '{filename}'. Ready to upload!"
)
