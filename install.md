# Ayatori Installation Guide

## Prerequisites

- [Conda](https://docs.conda.io/projects/conda/en/latest/user-guide/install/download.html) (Miniconda or Anaconda)
- Optional: [Mamba](https://mamba.readthedocs.io/en/latest/) for faster installation

## Installation (Windows/Linux/MacOS)

The recommended way to install dependencies is using Conda, especially for geospatial libraries like `geopandas` and `pyrosm` which can be difficult to install via pip on Windows.

### 1. Create Conda Environment

```bash
conda env create -f environment.yml
conda activate ayatori-gtfs-processor
```

Or using mamba:

```bash
mamba env create -f environment.yml
mamba activate ayatori-gtfs-processor
```

### 2. Install Project in Editable Mode

To use the `ayatori` package in your notebooks and scripts:

```bash
pip install -e .
```

## Windows Specific Notes

If you encounter issues installing `pyrosm` or `geopandas` on Windows:
1. Ensure you are using the `environment.yml` file as it pulls binaries from `conda-forge`.
2. If you must use pip, download the wheel files for `GDAL`, `Fiona`, and `Rtree` from [Christoph Gohlke's libs](https://www.lfd.uci.edu/~gohlke/pythonlibs/) before installing other requirements.

## Development Setup

To use the module inside your notebooks with auto-reloading:

```python
%load_ext autoreload
%autoreload 2
```

We use [nbdime](https://nbdime.readthedocs.io/en/stable/index.html) for diffing and merging Jupyter notebooks.

To configure it to this git project :

```
nbdime config-git --enable
```

To enable notebook extension :

```
nbdime extensions --enable --sys-prefix
```

Or, if you prefer full control, you can run the individual steps:

```
jupyter serverextension enable --py nbdime --sys-prefix

jupyter nbextension install --py nbdime --sys-prefix
jupyter nbextension enable --py nbdime --sys-prefix

jupyter labextension install nbdime-jupyterlab
```

You may need to rebuild the extension : `jupyter lab build`

## Set up Plotly for Jupyterlab

Plotly works in notebook but further steps are needed for it to work in Jupyterlab :

* @jupyter-widgets/jupyterlab-manager # Jupyter widgets support
* plotlywidget  # FigureWidget support
* @jupyterlab/plotly-extension  # offline iplot support

There are conflict versions between those extensions so check the [latest Plotly README](https://github.com/plotly/plotly.py#installation-of-plotlypy-version-3) to ensure you fetch the correct ones. 

```
jupyter labextension install @jupyter-widgets/jupyterlab-manager@0.36 --no-build
jupyter labextension install plotlywidget@0.2.1  --no-build
jupyter labextension install @jupyterlab/plotly-extension@0.16  --no-build
jupyter lab build
```

# Invoke command

We use [Invoke](http://www.pyinvoke.org/) to manage an
unique entry point into all of the project tasks.

List of all tasks for project :

```
$ invoke -l

Available tasks:

  lab     Launch Jupyter lab
```

Help on a particular task :

```
$ invoke --help lab
Usage: inv[oke] [--core-opts] notebook [--options] [other tasks here ...]

Docstring:
  Launch Jupyter lab

Options:
  -i STRING, --ip=STRING   IP to listen on, defaults to *
  -p, --port               Port to listen on, defaults to 8888
```

You will find the definition of each task inside the `tasks.py` file, so you can add your own.
