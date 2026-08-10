=== Sample Structure: config.json ===
{
  "week_start_day": "Monday",
  "meal_types": ["breakfast", "lunch", "dinner", "snack"],
  "weekly_schedule": {
    "Monday": {
      "calories": 1500,
      "protein_g": 120,
      "net_carbs_g": 50,
      "meal_overrides": {}
    },
    "Tuesday": {
      "calories": 1200,
      "protein_g": 120,
      "net_carbs_g": 50,
      "meal_overrides": {}
    },
    "Wednesday": {
      "calories": 1200,
      "protein_g": 120,
      "net_carbs_g": 50,
      "meal_overrides": {}
    },
    "Thursday": {
      "calories": 1200,
      "protein_g": 120,
      "net_carbs_g": 50,
      "meal_overrides": {}
    },
    "Friday": {
      "calories": 1500,
      "protein_g": 120,
      "net_carbs_g": 50,
      "meal_overrides": {}
    },
    "Saturday": {


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
  "generated_at": "2026-08-10T17:00:07.390809",
  "cook_events": [
    {
      "slot_id": "Monday:breakfast",
      "day": "Monday",
      "meal_type": "breakfast",
      "portions": 2,
      "style": "yoghurt_nuts_seeds",
      "cuisine": null,
      "eaten_by": [
        "Monday:breakfast"
      ],
      "recipe": {
        "name": "Greek Yoghurt Bowl with Walnuts, Pumpkin Seeds and Berries",
        "meal_type": "breakfast",
        "ingredients": [
          {
            "name": "Plain unsweetened Greek yoghurt (0% fat)",
            "quantity_g": 800.0,
            "nova_group": 1,
            "calories": 472.0,
            "protein_g": 80.0,
            "net_carbs_g": 32.0,
            "fat_g": 1.6


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
    "day_of_week": "Tuesday",
    "generated_at": "2026-08-10T15:03:41.907942",
    "cuisine": "middle_eastern",
    "styles": {
      "breakfast": "eggs",
      "dinner": "grill"
    },
    "main_proteins": [
      "boneless skinless chicken thigh"
    ],
    "recipe_names": [
      "Egg White and Herb Scramble",
      "Grilled Za'atar Chicken Thighs with Charred Vegetables and Tahini Sauce"
    ]
  },
  {
    "day_of_week": "Wednesday",
    "generated_at": "2026-08-10T15:03:41.907942",
    "cuisine": "italian",
    "styles": {
      "breakfast": "shake",
      "dinner": "stir_fry"
    },
    "main_proteins": [
      "Italian pork sausage, casing removed"
    ],
    "recipe_names": [
      "Vanilla Almond Protein Shake with Blueberries",
      "Italian Sausage, Cannellini and Broccoli Rabe Stir Fry"
    ]
  },
  {
    "day_of_week": "Thursday",


=== Model Schema: WeekPlan.sunday_prep_session (planner.SundayPrepSession) ===
{
  "$defs": {
    "PrepPhase": {
      "description": "One step in the Sunday batch-prep timeline, run in order.",
      "properties": {
        "name": {
          "title": "Name",
          "type": "string"
        },
        "description": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Description"
        },
        "active_minutes": {
          "default": 0,
          "title": "Active Minutes",
          "type": "integer"
        },
        "passive_minutes": {
          "default": 0,
          "title": "Passive Minutes",
          "type": "integer"
        }
      },
      "required": [
        "name"
      ],
      "title": "PrepPhase",
      "type": "object"
    }
  },
  "description": "Optional Sunday batch-prep plan: raw prep work aggregated across the\nweek's cook events (e.g. \"dice all onions\" once instead of per cook day),\ndone ahead of time rather than repeated on each cook day.\n\n`total_active_minutes` is capped at 120 to match config's\n`max_prep_active_mins` default \u2014 hands-on prep time, not the passive\nminutes spent simmering/roasting/chilling while unattended.",
  "properties": {
    "total_active_minutes": {
      "maximum": 120,
      "title": "Total Active Minutes",
      "type": "integer"
    },
    "total_passive_minutes": {
      "default": 0,
      "title": "Total Passive Minutes",
      "type": "integer"
    },
    "aggregated_ingredients": {
      "additionalProperties": {
        "type": "string"
      },
      "title": "Aggregated Ingredients",
      "type": "object"
    },
    "timeline": {
      "items": {
        "$ref": "#/$defs/PrepPhase"
      },
      "title": "Timeline",
      "type": "array"
    }
  },
  "required": [
    "total_active_minutes"
  ],
  "title": "SundayPrepSession",
  "type": "object"
}


