"""Developer-only: attach Whisker (Pipecat's live debugger) to jarvisd's voice pipelines.

Not installed and never loaded by default. Pipecat's WorkerRunner reads setup files named in
PIPECAT_SETUP_FILES, so no Jarvis code changes. To use it for one debugging session:

    ~/.local/share/jarvis-workos/venv/bin/pip install pipecat-ai-whisker   # network, unpinned: dev only
    systemctl --user set-environment PIPECAT_SETUP_FILES=$PWD/omarchy/dev/whisker_setup.py
    systemctl --user restart jarvisd
    # open the Whisker UI against ws://localhost:9090 (see https://github.com/pipecat-ai/whisker)

Undo: systemctl --user unset-environment PIPECAT_SETUP_FILES && systemctl --user restart jarvisd

Whisker shows every frame, including transcripts and tool arguments: only on your own machine.
"""

from pipecat_whisker import WhiskerServer

whisker = WhiskerServer()


async def setup_worker_runner(runner):
    await runner.add_workers(whisker)


async def setup_pipeline_worker(worker):
    worker.add_observer(whisker.create_observer(worker))
