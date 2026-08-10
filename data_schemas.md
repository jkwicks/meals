=== Sample Structure: config.json ===
{
  "week_start_day": "Monday",
  "meal_types": ["breakfast", "lunch", "dinner", "snack"],
"weekly_schedule": {
  "Monday": {
    "calories": 1500,
    "protein_g": 120,
    "net_carbs_g": 130,
    "fat_g": 55,
    "meal_overrides": {
      "breakfast": { "calories": 400, "protein_g": 30, "net_carbs_g": 35, "fat_g": 15 }
    }
  },
  "Tuesday": {
    "calories": 1200,
    "protein_g": 115,
    "net_carbs_g": 85,
    "fat_g": 44,
    "meal_overrides": {
      "breakfast": { "calories": 400, "protein_g": 30, "net_carbs_g": 35, "fat_g": 15 }
    }
  },
  "Wednesday": {
    "calories": 1000,
    "protein_g": 110,
    "net_carbs_g": 60,
    "fat_g": 35,
    "meal_overrides": {
      "breakfast": { "calories": 350, "protein_g": 30, "net_carbs_g": 25, "fat_g": 12 }
    }
  },
  "Thursday": {
    "calories": 1000,
    "protein_g": 110,
    "net_carbs_g": 60,


=== Sample Structure: week_plan.json ===
{
  "days": [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday"
  ],
  "servings_per_meal": 2,
  "generated_at": "2026-08-11T08:48:35.484539",
  "cook_events": [
    {
      "slot_id": "Monday:breakfast",
      "day": "Monday",
      "meal_type": "breakfast",
      "portions": 2,
      "style": "yoghurt_bowl",
      "cuisine": null,
      "eaten_by": [
        "Monday:breakfast"
      ],
      "recipe": {
        "name": "Greek Yoghurt Berry Seed Bowl with Protein Boost",
        "meal_type": "breakfast",
        "ingredients": [
          {
            "name": "Plain Greek yoghurt",
            "quantity_g": 200.0,
            "nova_group": 1,
            "calories": 156.0,
            "protein_g": 25.0,
            "net_carbs_g": 8.8,
            "fat_g": 1.0


=== Sample Structure: week_plan_next.json ===
{
  "days": [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday"
  ],
  "servings_per_meal": 2,
  "generated_at": "2026-08-10T20:16:21.325401",
  "cook_events": [
    {
      "slot_id": "Monday:breakfast",
      "day": "Monday",
      "meal_type": "breakfast",
      "portions": 2,
      "style": "baked_beans",
      "cuisine": null,
      "eaten_by": [
        "Monday:breakfast"
      ],
      "recipe": {
        "name": "White Bean Baked Beans with Turkey Bacon and Egg",
        "meal_type": "breakfast",
        "ingredients": [
          {
            "name": "White beans, cooked",
            "quantity_g": 400.0,
            "nova_group": 1,
            "calories": 290.0,
            "protein_g": 20.0,
            "net_carbs_g": 36.0,
            "fat_g": 1.0


=== Sample Structure: meal_plan.json ===
{
  "day_of_week": "Sunday",
  "target_calories": 1500.0,
  "target_protein_g": 110.0,
  "target_net_carbs_g": 50.0,
  "target_fat_g": 137.8,
  "is_keto": true,
  "recipes": [
    {
      "name": "Batch Scrambled Eggs with Avocado Oil and Cheddar",
      "meal_type": "breakfast",
      "ingredients": [
        {
          "name": "Egg",
          "quantity_g": 200.0,
          "nova_group": 1,
          "calories": 284.0,
          "protein_g": 25.0,
          "net_carbs_g": 1.4,
          "fat_g": 19.0
        },
        {
          "name": "Cheddar Cheese",
          "quantity_g": 30.0,
          "nova_group": 3,
          "calories": 122.0,
          "protein_g": 7.0,
          "net_carbs_g": 0.5,
          "fat_g": 10.0
        },
        {
          "name": "Avocado Oil",
          "quantity_g": 15.0,
          "nova_group": 2,
          "calories": 135.0,


=== Sample Structure: meal_history.json ===
[
  {
    "day_of_week": "Monday",
    "generated_at": "2026-08-10T20:03:21.493208",
    "cuisine": "mexican",
    "styles": {
      "breakfast": "yoghurt_nuts_seeds",
      "lunch": "salad",
      "dinner": "grill"
    },
    "main_proteins": [
      "Chicken breast, skinless",
      "Flank steak"
    ],
    "recipe_names": [
      "Greek Yoghurt Bowl with Walnuts, Chia and Vanilla Protein",
      "Grilled Chicken and Chickpea Power Salad",
      "Carne Asada with Cilantro-Lime Rice and Charred Peppers"
    ]
  },
  {
    "day_of_week": "Tuesday",
    "generated_at": "2026-08-10T20:03:21.493208",
    "cuisine": "indian",
    "styles": {
      "breakfast": "fish",
      "dinner": "roast"
    },
    "main_proteins": [
      "Chicken thighs, skin-on, boneless"
    ],
    "recipe_names": [
      "Peppered Mackerel with Lemon-Yoghurt and Cucumber",
      "Tandoori-Style Roast Chicken Thighs with Roasted Cauliflower and Spinach"
    ]


=== Sample Structure: whfoods.json ===
[
  {
    "food_id": "asparagus",
    "name": "Asparagus",
    "category": "Vegetables",
    "optimal_cooking": {
      "recommended_method": "Steam",
      "duration_minutes": 5,
      "prep_tips": [
        "Trim ends and steam for 5 minutes until tender-crisp."
      ]
    }
  },
  {
    "food_id": "avocado",
    "name": "Avocado",
    "category": "Vegetables",
    "optimal_cooking": {
      "recommended_method": "Raw",
      "duration_minutes": null,
      "prep_tips": [
        "Eat raw; pair with carotenoid-rich foods to quadruple nutrient absorption."
      ]
    }
  },
  {
    "food_id": "beets",
    "name": "Beets",
    "category": "Vegetables",
    "optimal_cooking": {
      "recommended_method": "Steam",
      "duration_minutes": 15,
      "prep_tips": [
        "Cut into 1/2-inch wedges and steam for 15 minutes."
      ]


=== Sample Structure: recipes_master.json ===
[
  {
    "id": "d0757d64d0d244db9d801d95131d42b8",
    "content_key": "b7a30c249a357f6c",
    "recipe": {
      "name": "Spinach, Feta and Turkey Scramble",
      "meal_type": "breakfast",
      "ingredients": [
        {
          "name": "eggs",
          "quantity_g": 228.0,
          "nova_group": 1,
          "calories": 352.6,
          "protein_g": 28.8,
          "net_carbs_g": 1.8,
          "fat_g": 24.4
        },
        {
          "name": "ground turkey breast, raw",
          "quantity_g": 122.0,
          "nova_group": 1,
          "calories": 152.0,
          "protein_g": 32.0,
          "net_carbs_g": 0.0,
          "fat_g": 3.0
        },
        {
          "name": "baby spinach",
          "quantity_g": 92.0,
          "nova_group": 1,
          "calories": 21.2,
          "protein_g": 2.6,
          "net_carbs_g": 1.0,
          "fat_g": 0.4
        },


=== Sample Structure: favorites.json ===
[
  {
    "id": "d0757d64d0d244db9d801d95131d42b8",
    "saved_at": "2026-08-09T05:36:08+00:00",
    "recipe": {
      "name": "Spinach, Feta and Turkey Scramble",
      "meal_type": "breakfast",
      "ingredients": [
        {
          "name": "eggs",
          "quantity_g": 228.0,
          "nova_group": 1,
          "calories": 352.6,
          "protein_g": 28.8,
          "net_carbs_g": 1.8,
          "fat_g": 24.4
        },
        {
          "name": "ground turkey breast, raw",
          "quantity_g": 122.0,
          "nova_group": 1,
          "calories": 152.0,
          "protein_g": 32.0,
          "net_carbs_g": 0.0,
          "fat_g": 3.0
        },
        {
          "name": "baby spinach",
          "quantity_g": 92.0,
          "nova_group": 1,
          "calories": 21.2,
          "protein_g": 2.6,
          "net_carbs_g": 1.0,
          "fat_g": 0.4
        },


=== Sample Structure: models.json ===
{
  "default_planner_model": "anthropic/claude-sonnet-5",
  "quick_swap_model": "google/gemini-3.6-flash",
  "vision_model": "google/gemini-3.6-flash",
  "recipe_parser_model": "google/gemini-3.6-flash",
  "openrouter_base_url": "https://openrouter.ai/api/v1",
  "request_timeout_seconds": 120.0,
  "max_tokens": {
    "weekly_planner": 16000,
    "quick_swap": 4000
  },
  "selectable_options": [
    "anthropic/claude-sonnet-5",
    "google/gemini-3.6-flash",
    "openai/gpt-5",
    "google/gemma-4-26b-a4b-it:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free"
  ]
}


=== Sample Structure: openrouter_top_50.csv ===
ID,Name,Context Length,Prompt Price (per 1M),Completion Price (per 1M)
inclusionai/ling-3.0-tiny:free,inclusionAI: Ling 3.0 Tiny (free),262144,$0.0000,$0.0000
meta/muse-spark-1.2,Meta: Muse Spark 1.2,1048576,$1.2500,$4.2500
qwen/qwen3.8-max,Qwen: Qwen3.8 Max,1000000,$2.0000,$6.0000
~deepseek/deepseek-v4-flash-latest,DeepSeek V4 Flash Latest,1048576,$0.0800,$0.2520
deepseek/deepseek-v4-flash-0731,DeepSeek: DeepSeek V4 Flash 0731,1048576,$0.0900,$0.1800
thinkingmachines/inkling-small,Thinking Machines: Inkling Small,524288,$0.4500,$1.2000
qwen/qwen3.7-flash,Qwen: Qwen3.7 Flash,1000000,$0.0300,$0.1300
anthropic/claude-opus-5-fast,Claude Opus 5 (Fast),1000000,$10.0000,$50.0000
anthropic/claude-opus-5,Claude Opus 5,1000000,$5.0000,$25.0000
anthropic/claude-opus-5:batch,Claude Opus 5 (batch),1000000,$2.5000,$12.5000
inclusionai/ling-3.0-flash,Ling-3.0-flash,262144,$0.0210,$0.0630
poolside/laguna-s-2.1,Poolside: Laguna S 2.1,1048576,$0.0900,$0.1800
poolside/laguna-s-2.1:free,Poolside: Laguna S 2.1 (free),262144,$0.0000,$0.0000
google/gemini-3.6-flash,Google: Gemini 3.6 Flash,1048576,$1.5000,$7.5000
google/gemini-3.6-flash:batch,Google: Gemini 3.6 Flash (batch),1048576,$0.7500,$3.7500
google/gemini-3.5-flash-lite,Google: Gemini 3.5 Flash Lite,1048576,$0.3000,$2.5000
google/gemini-3.5-flash-lite:batch,Google: Gemini 3.5 Flash Lite (batch),1048576,$0.1500,$1.2500
meituan/longcat-2.0,Meituan: LongCat 2.0,1048756,$0.3000,$1.2000
thinkingmachines/inkling,Thinking Machines: Inkling,1048576,$0.9500,$4.0500
thinkingmachines/inkling:batch,Thinking Machines: Inkling (batch),524288,$0.5000,$2.0250
openrouter/auto-beta,Auto Router (Beta),2000000,$-1000000.0000,$-1000000.0000
moonshotai/kimi-k3,MoonshotAI: Kimi K3,1048576,$3.0000,$15.0000
meta/muse-spark-1.1,Meta: Muse Spark 1.1,1048576,$1.2500,$4.2500
kwaipilot/kat-coder-air-v2.5,Kwaipilot: KAT-Coder-Air V2.5,256000,$0.1500,$0.6000
kwaipilot/kat-coder-pro-v2.5,Kwaipilot: KAT-Coder-Pro V2.5,256000,$0.7400,$2.9600
openai/gpt-5.6-luna-pro,OpenAI: GPT-5.6 Luna Pro,1050000,$0.1000,$0.6000
openai/gpt-5.6-luna-pro:batch,OpenAI: GPT-5.6 Luna Pro (batch),1050000,$0.1000,$0.6000
openai/gpt-5.6-luna,OpenAI: GPT-5.6 Luna,1050000,$0.1000,$0.6000
openai/gpt-5.6-luna:batch,OpenAI: GPT-5.6 Luna (batch),1050000,$0.1000,$0.6000
openai/gpt-5.6-terra-pro,OpenAI: GPT-5.6 Terra Pro,1050000,$1.0000,$6.0000
openai/gpt-5.6-terra-pro:batch,OpenAI: GPT-5.6 Terra Pro (batch),1050000,$1.0000,$6.0000
openai/gpt-5.6-terra,OpenAI: GPT-5.6 Terra,1050000,$1.0000,$6.0000
openai/gpt-5.6-terra:batch,OpenAI: GPT-5.6 Terra (batch),1050000,$1.0000,$6.0000
openai/gpt-5.6-sol-pro,OpenAI: GPT-5.6 Sol Pro,1050000,$5.0000,$30.0000


