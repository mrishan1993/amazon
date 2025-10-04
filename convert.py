import csv
import yaml
import os
import io

# --- Configuration ---
CSV_FILENAME = 'Amazon.csv'
YAML_FILENAME = 'output_data.yaml'
DOMAIN = 'https://www.amazon.in'
ASIN_COLUMN_INDEX = 1
KEYWORDS_COLUMN_INDEX = 2
KEYWORD_DELIMITER = ';'
# Use 'latin-1' as the source encoding to handle the 0x96 byte error
SOURCE_ENCODING = 'latin-1'

def convert_csv_to_yaml():
    """Reads the CSV, converts the data, and writes it to a YAML file."""

    if not os.path.exists(CSV_FILENAME):
        print(f"Error: CSV file '{CSV_FILENAME}' not found. Please ensure your CSV is named 'Amazon.csv'.")
        return

    data_list = []

    try:
        # STEP 1: Open the file using the necessary 'latin-1' encoding.
        # This handles the "codec can't decode byte 0x96" error.
        with open(CSV_FILENAME, mode='r', encoding=SOURCE_ENCODING, newline='') as file:
            # Read all content and replace NUL characters, if any still exist.
            content = file.read().replace('\x00', '')

        # STEP 2: Use io.StringIO to treat the cleaned string content as a file for csv.reader.
        csvfile = io.StringIO(content)
        reader = csv.reader(csvfile)

        # Skip the header row
        try:
            next(reader)
        except StopIteration:
            print("Error: The CSV file appears to be empty.")
            return

        # STEP 3: Process the data
        for row in reader:
            if len(row) > KEYWORDS_COLUMN_INDEX:
                # Decode and strip the ASIN and Keywords
                # The data is already in 'latin-1' string format from Step 1
                asin = row[ASIN_COLUMN_INDEX].strip()
                raw_keywords = row[KEYWORDS_COLUMN_INDEX]

                if not asin or not raw_keywords:
                    continue

                # Split keywords by the semicolon delimiter (';') and clean up whitespace
                keywords = [
                    kw.strip()
                    for kw in raw_keywords.split(KEYWORD_DELIMITER)
                    if kw.strip()
                ]

                # Construct the final YAML item structure
                item = {
                    'asin': asin,
                    'domain': DOMAIN,
                    'keywords': keywords
                }
                data_list.append(item)

        # STEP 4: Write the list of dictionaries to a YAML file using UTF-8 (standard for output)
        with open(YAML_FILENAME, 'w', encoding='utf-8') as outfile:
            yaml.dump(data_list, outfile, default_flow_style=False, sort_keys=False)

        print(f"✅ Success! Converted {len(data_list)} ASINs to YAML.")
        print(f"File saved as '{YAML_FILENAME}'.")

    except Exception as e:
        print(f"An unexpected error occurred during processing: {e}")

if __name__ == "__main__":
    convert_csv_to_yaml()