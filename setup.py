import os

from setuptools import setup, find_packages

def readme() -> str:
    """Utility function to read the README.md.

    Used for the `long_description`. It's nice, because now
    1) we have a top level README file and
    2) it's easier to type in the README file than to put a raw string in below.

    Args:
        nothing

    Returns:
        String of readed README.md file.
    """
    return open(os.path.join(os.path.dirname(__file__), 'README.md')).read()

setup(
    name='ayatori',
    version='0.1.0',
    author='Felipe Leal',
    author_email='pipelealcerro@gmail.com',
    description='Python module that processes public transport information (in GTFS format) to create route generation algorithms.',
    python_requires='>=3',
    license='MIT',
    url='',
    packages=find_packages(),
    install_requires=[
        'numpy',
        'pandas',
        'networkx',
        'geopy',
        'pygtfs',
        'python-dotenv',
    ],
    extras_require={
        'dev': ['black', 'pylint', 'invoke', 'jupyterlab'],
        'viz': ['matplotlib', 'seaborn', 'folium', 'plotly'],
        'geo': ['geopandas', 'pyrosm'],
    },
    long_description=readme(),
)