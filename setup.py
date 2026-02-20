from setuptools import setup, find_packages

setup(
    name="csv-traitement",
    version="0.1.0",
    description="Détection et correction automatique des anomalies CSV",
    author="Patrice Duclos",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "xlsxwriter>=3.1.0",
        "chardet>=5.0.0",
    ],
    extras_require={
        "dev": ["pytest>=7.0.0"],
    },
    entry_points={
        "console_scripts": [
            "csvtraitement=main:main",
        ],
    },
)
