Slack Repository Notifications
==============================

`Homepage<https://gitlab.com/Fuyukai/repo-notifier>`_

This is a simple bot that receives incoming webhook data from GitLab and passes it to GitHub.

Setup
-----

To set up the notifier, install the dependencies using Pipenv_::

    $ pipenv install


Usage
-----

To use the script, invoke ``server.py`` with your slack bot token as the first argument.
Optionally, you can provide a channel name as the second argument, otherwise it defaults to
``repo-report``.

.. _Pipenv: https://docs.pipenv.org/