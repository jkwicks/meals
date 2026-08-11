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
  "generated_at": "2026-08-11T19:36:13.650094",
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
        "name": "Papaya Plum Yoghurt Bowl with Hemp and Walnuts",
        "meal_type": "breakfast",
        "ingredients": [
          {
            "name": "Greek yoghurt, plain",
            "quantity_g": 200.0,
            "nova_group": 1,
            "calories": 184.2,
            "protein_g": 24.2,
            "net_carbs_g": 8.2,
            "fat_g": 5.6


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
    "generated_at": "2026-08-11T13:54:56.801174",
    "cuisine": "mexican",
    "styles": {
      "breakfast": "yoghurt_bowl",
      "lunch": "salad",
      "dinner": "grill",
      "snack": "boiled_eggs"
    },
    "main_proteins": [
      "Salmon fillet, skin-on",
      "Turkey breast steak, raw"
    ],
    "recipe_names": [
      "Kiwi Raspberry Yoghurt Bowl with Seed Mix",
      "Salmon and Black Bean Salad with Fennel and Mustard Greens",
      "Grilled Turkey Tacos with Black Bean-Kidney Bean Rice and Charred Salsa",
      "Peppermint and Parsley Seasoned Boiled Eggs"
    ]
  },
  {
    "day_of_week": "Tuesday",
    "generated_at": "2026-08-11T13:54:56.801174",
    "cuisine": "indian",
    "styles": {
      "breakfast": "fish_pate",
      "dinner": "roast",
      "snack": "nuts_seeds"
    },
    "main_proteins": [
      "Turkey breast, raw, diced"
    ],
    "recipe_names": [


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
    },
    "meals_included": {
      "description": "Names of the dishes this prep session covers",
      "items": {
        "type": "string"
      },
      "title": "Meals Included",
      "type": "array"
    }
  },
  "required": [
    "total_active_minutes"
  ],
  "title": "SundayPrepSession",
  "type": "object"
}


