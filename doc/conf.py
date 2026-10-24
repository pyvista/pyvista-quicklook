"""PyVista Quick Look documentation configuration."""

from __future__ import annotations

from datetime import datetime
from importlib.metadata import version as _metadata_version

extensions = [
    'notfound.extension',
    'sphinx_copybutton',
    'sphinx_design',
]

source_suffix = '.rst'
master_doc = 'index'

project = 'PyVista Quick Look'
copyright = f'{datetime.now().year}, PyVista'  # noqa: A001, DTZ005
author = 'The PyVista developers'
release = version = _metadata_version('pyvista-quicklook')

language = 'en'
exclude_patterns = ['_build']
pygments_style = 'sphinx'

# -- Options for HTML output --------------------------------------------------

html_theme = 'sphinx_book_theme'
html_title = 'PyVista Quick Look'
html_logo = '_static/logo.png'
html_favicon = '_static/logo.png'
html_context = {
    'github_user': 'pyvista',
    'github_repo': 'pyvista-quicklook',
    'github_version': 'main',
    'doc_path': 'doc',
}
html_show_sourcelink = False
html_baseurl = 'https://pyvista.github.io/pyvista-quicklook/'
notfound_urls_prefix = '/pyvista-quicklook/'

html_theme_options = {
    'show_prev_next': False,
    'github_url': 'https://github.com/pyvista/pyvista-quicklook',
    'collapse_navigation': True,
    'use_edit_page_button': True,
    'navigation_with_keys': False,
    'show_navbar_depth': 1,
    'icon_links': [
        {
            'name': 'Slack Community',
            'url': 'https://communityinviter.com/apps/pyvista/pyvista',
            'icon': 'fab fa-slack',
        },
        {
            'name': 'Support',
            'url': 'https://github.com/pyvista/pyvista/discussions',
            'icon': 'fa fa-comment fa-fw',
        },
        {
            'name': 'PyPI',
            'url': 'https://pypi.org/project/pyvista-quicklook',
            'icon': 'fa-brands fa-python',
        },
    ],
}

html_static_path = ['_static']
