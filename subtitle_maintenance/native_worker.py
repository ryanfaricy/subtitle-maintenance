"""Short-lived process releases Metal memory after transcription."""
import json
import sys
import mlx.core as mx
import mlx_whisper
mx.set_cache_limit(512*1024*1024)
data=mlx_whisper.transcribe(sys.argv[1],path_or_hf_repo=sys.argv[3],language='en',
                          word_timestamps=True,condition_on_previous_text=False,verbose=False)
with open(sys.argv[2],'w') as f:json.dump(data,f)
