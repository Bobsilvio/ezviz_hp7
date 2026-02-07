#!/bin/bash
cd /Users/eleonor/italysat/ezviz_hp7/custom_components/ezviz_hp7

echo "=== SENSORI vs TRADUZIONI ===" 
echo
echo "🔍 SENSORI DEFINITI (sensor.py):"
grep -n "^    (\"" sensor.py | head -15

echo
echo "🔍 TRADUZIONI SENSORI (strings.json):"
grep -o '"[a-z_]*": {' translations/strings.json | grep -v config | grep -v button | grep -v service | head -15

echo
echo "🔍 BINARY SENSORI (binary_sensor.py):"
grep "_attr_translation_key\|_attr_name = " binary_sensor.py | head -10

echo
echo "🔍 TRADUZIONI BINARY (strings.json):"
grep -A1 '"binary_sensor"' translations/strings.json | head -20

