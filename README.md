# PAI2 Peak Analyzer

This application reads data from a .pai2 file (MessagePack format) and displays peak information as a scatter plot.

## Requirements

- Python 3.13+
- msgpack
- lz4
- numpy
- matplotlib

## Usage

Run the script with a .pai2 file as argument:

```
python analyze_pai2.py path/to/your/file.pai2
```

The script will display a scatter plot with:
- X-axis: Retention Time (RT)
- Y-axis: m/z
- Point size: Proportional to log(intensity)

## Data Format

The .pai2 file is expected to contain a list of dictionaries, each with keys:
- 'mz' or 'mass': m/z value
- 'rt' or 'retention_time': Retention time
- 'intensity': Peak intensity (optional, defaults to 1)

## Notes

- If no valid peak data is found, the script will exit with an error message.
- The plot uses matplotlib's interactive mode.