cd /Users/wicked/x/codeseeq                                                                                                 
CODESEEQ_RUNTIME_MODE=host \                                                                                                
CODESEEQ_BRIDGE_MODE=process \                                                                                              
CODESEEQ_GGUF_N_GPU_LAYERS=99 \                                                                                             
./codeseeq --model ~/Qoding/ai/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf "say hi"     


ok so I want to be able to run it this way, but also make sure we can set the following configuration parameters:
  -np 1
  -ngl all
  -c 131072
  --port 8888
in config as well as ENV VARS please.
