=== Sample Structure: config.json ===
{
  "openrouter_model": "deepseek/deepseek-v4-flash",
  "week_start_day": "Monday",
  "meal_types": ["breakfast", "lunch", "dinner", "snack"],
  "weekly_schedule": {
    "Monday": {
      "calories": 2200,
      "protein_g": 170,
      "net_carbs_g": 150,
      "meal_overrides": {}
    },
    "Tuesday": {
      "calories": 2200,
      "protein_g": 170,
      "net_carbs_g": 150,
      "meal_overrides": {}
    },
    "Wednesday": {
      "calories": 2000,
      "protein_g": 160,
      "net_carbs_g": 30,
      "meal_overrides": {}
    },
    "Thursday": {
      "calories": 2200,
      "protein_g": 170,
      "net_carbs_g": 150,
      "meal_overrides": {}
    },
    "Friday": {
      "calories": 1543,
      "protein_g": 160,
      "net_carbs_g": 30,
      "meal_overrides": {}
    },
-e 

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
  "generated_at": "2026-08-08T20:16:50.125936",
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
        "name": "Protein-Packed Baked White Beans with Egg and Greens",
        "meal_type": "breakfast",
        "ingredients": [
          {
            "name": "canned white beans (no salt added)",
            "quantity_g": 272,
            "nova_group": 3,
            "calories": 325.4,
            "protein_g": 21.2,
            "net_carbs_g": 43.4,
            "fat_g": 1.4
-e 

=== Sample Structure: meal_plan.json ===
{
  "day_of_week": "Sunday",
  "target_calories": 2000.0,
  "target_protein_g": 160.0,
  "target_net_carbs_g": 30.0,
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
-e 

=== Sample Structure: meal_history.json ===
[
  {
    "day_of_week": "Monday",
    "generated_at": "2026-08-08T08:58:38.128339",
    "cuisine": "bbq",
    "styles": {
      "breakfast": "yoghurt_nuts_seeds",
      "lunch": "soup",
      "dinner": "curry"
    },
    "main_proteins": [
      "chicken breast, boneless skinless",
      "beef chuck, cubed"
    ],
    "recipe_names": [
      "Yoghurt Bowl with Nuts, Seeds and Berries",
      "Hearty Chicken and Vegetable Soup",
      "BBQ Beef Curry with Smoky Pepper and Sweet Potato"
    ]
  },
  {
    "day_of_week": "Tuesday",
    "generated_at": "2026-08-08T08:58:38.128339",
    "cuisine": "cajun",
    "styles": {
      "breakfast": "fish",
      "dinner": "one_pan"
    },
    "main_proteins": [
      "Chicken thighs, skinless, boneless"
    ],
    "recipe_names": [
      "Smoked Salmon & Avocado Scramble",
      "Cajun Blackened Chicken & Vegetable Traybake"
    ]
-e 

