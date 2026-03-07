# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = 'AgentShip'
copyright = '2026, AgentShip Contributors'
author = 'AgentShip Contributors'

version = '1.0.0'
release = '1.0.0'

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

import os
import sys
sys.path.insert(0, os.path.abspath('../../'))

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.viewcode',
    'sphinx.ext.intersphinx',
    'sphinx.ext.napoleon',  # Google/NumPy style docstrings
    'sphinx_autodoc_typehints',  # Type hints in docs
    'myst_parser',  # Markdown support (for user guides from docs/)
]

# Add napoleon extension (it's built-in but needs to be imported)
try:
    import sphinx.ext.napoleon
except ImportError:
    pass

templates_path = ['_templates']
exclude_patterns = []

# Support both RST and Markdown files
source_suffix = {
    '.rst': 'restructuredtext',
    '.md': 'markdown',
}

language = 'en'

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

# Use furo theme (modern, compatible with Sphinx 9+)
html_theme = 'furo'
html_static_path = ['_static']

# Branding
html_logo = '_static/logo-icon.png'
html_favicon = '_static/favicon-32.png'


# Sidebar configuration - ensure consistent navigation across all pages
# This forces the master toctree from index.rst to always be shown
# Right sidebar (table of contents) is disabled - navigation only via left sidebar
html_sidebars = {
    '**': [
        'sidebar/scroll-start.html',
        'sidebar/brand.html',
        'sidebar/search.html',
        'sidebar/navigation.html',  # Shows master toctree from index.rst
        'sidebar/ethical-ads.html',
        'sidebar/scroll-end.html',
    ]
}

# Theme options – AgentShip branded colors
html_theme_options = {
    'sidebar_hide_name': False,
    'navigation_with_keys': True,
    'top_of_page_button': 'edit',
    'show_navbar_depth': 1,
    'toc_title': None,
    'dark_css_variables': {
        'color-brand-primary': '#818cf8',
        'color-brand-content': '#a78bfa',
    },
    'light_css_variables': {
        'color-brand-primary': '#7c3aed',
        'color-brand-content': '#6d28d9',
    },
    'footer_icons': [
        {
            'name': 'GitHub',
            'url': 'https://github.com/Agent-Ship/agent-ship',
            'html': '<svg stroke="currentColor" fill="currentColor" stroke-width="0" viewBox="0 0 16 16"><path fill-rule="evenodd" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.42 7.42 0 014 0c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0016 8c0-4.42-3.58-8-8-8z"></path></svg>',
            'class': '',
        },
    ],
}

# Load Inter font
html_css_files = [
    'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap',
    'custom.css',
]

# -- Autodoc configuration ---------------------------------------------------
autodoc_default_options = {
    'members': True,
    'member-order': 'bysource',
    'special-members': '__init__',
    'undoc-members': False,  # Don't show undocumented members
    'exclude-members': '__weakref__'
}
autodoc_mock_imports = ['google.adk', 'google.genai', 'opik', 'litellm']

# Suppress forward reference warnings
typehints_fully_qualified = False
always_document_param_types = False

# -- Napoleon configuration (for Google-style docstrings) -------------------
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = False
napoleon_include_private_with_doc = False
napoleon_use_admonition_for_examples = False
napoleon_use_admonition_for_notes = False
napoleon_use_admonition_for_references = False
napoleon_use_ivar = False
napoleon_use_param = True
napoleon_use_rtype = True
napoleon_preprocess_types = False
napoleon_type_aliases = None
napoleon_attr_annotations = True

# -- Options for intersphinx extension ---------------------------------------
# https://www.sphinx-doc.org/en/master/usage/extensions/intersphinx.html#configuration

intersphinx_mapping = {
    'python': ('https://docs.python.org/3', None),
}
