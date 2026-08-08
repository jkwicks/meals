=== File Structure: config.json ===
{
  "openrouter_model": "deepseek/deepseek-v4-flash",
  "week_start_day": "Monday",
  "meal_types": ["breakfast", "lunch", "dinner", "snack"],
  "weekly_schedule": {
    "Monday": {
      "calories": 2200,
      "protein_g": 170,
      "net_carbs_g": 150
    },
    "Tuesday": {
      "calories": 2200,
      "protein_g": 170,
      "net_carbs_g": 150
    },
    "Wednesday": {
      "calories": 2000,
      "protein_g": 160,
      "net_carbs_g": 30
    },
    "Thursday": {
      "calories": 2200,
      "protein_g": 170,
      "net_carbs_g": 150
    },
    "Friday": {
      "calories": 1543,
      "protein_g": 160,
      "net_carbs_g": 30
    },


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
  "generated_at": "2026-08-08T09:10:26.124153",
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
        "name": "Hearty Baked Beans with Poached Egg and Turkey Sausage",
        "meal_type": "breakfast",
        "ingredients": [
          {
            "name": "canned baked beans (low sugar, no HFCS)",
            "quantity_g": 360,


