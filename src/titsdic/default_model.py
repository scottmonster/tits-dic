
from pywhispercpp.model import Model as WhisperModel



def defaultModel() -> WhisperModel:
    # top-level constructor defaults  
    return WhisperModel(
        # model name or direct ggml model file path
        model="tiny.en",                     
        # directory used to find or download named models
        models_dir="",                       
     
        # keep whisper.cpp stderr logging unredirected
        redirect_whispercpp_logs_to=False,    
        # enable OpenVINO encoder path
        # use_openvino=False,                   
        # explicit OpenVINO model path when using OpenVINO
        # openvino_model_path="",             
        # OpenVINO target device name
        # openvino_device="CPU",              
        # OpenVINO cache directoryopenvino_cache_dir="",              


    
       
        context_params={
            # enable GPU acceleration when available    
            "use_gpu": True,                
            # enable flash attention when supported by the backend
            "flash_attn": False,            
            # GPU device index to use
            "gpu_device": 0,                
            # enable experimental DTW token timestamp alignment
            "dtw_token_timestamps": False,  
            # DTW alignment-head preset enum value
            "dtw_aheads_preset": 0,         
            # number of top text layers used by DTW presets
            "dtw_n_top": 0,                 
            # experimental DTW memory override"dtw_mem_size": 0,              
        },

        
        # 0 = greedy sampling, nonzero = beam search sampling
        params_sampling_strategy=0,      
        # greedy decoder settings; -1 leaves library defaults in place
        greedy={ "best_of": -1 },
        # beam-search decoder settings; -1 leaves library defaults in place},         
        beam_search={
            "beam_size": -1,
            "patience": -1.0,
        },
                                           


     
        # decode defaults   
        # inference thread count; None lets whisper.cpp choose
        n_threads=None,                       
        # max prompt/history tokens to carry forward
        n_max_text_ctx=16384,                
        # start processing this many milliseconds into the audio
        offset_ms=0,                         
        # process only this many milliseconds; 0 means full audio
        duration_ms=0,                       
        # translate speech to English instead of transcribing source language
        translate=False,                     
        # do not reuse previous text as decoder context
        no_context=False,                    
        # omit timestamps from decode output
        no_timestamps=False,                 
        # force output as a single segment when possible
        single_segment=False,                
        # include special tokens in printed output
        print_special=False,                 
        # show decode progress output
        print_progress=True,                 
        # print segments as they are produced
        print_realtime=False,                
        # include timestamps in realtime printed output
        print_timestamps=True,               
        # enable token-level timestamps in the decode path
        token_timestamps=False,              
        # max characters per segment; 0 means unlimited
        max_len=0,                           
        # split long segments on word boundaries instead of token boundaries
        split_on_word=False,                 
        # max tokens per segment; 0 means unlimited
        max_tokens=0,                        
        # enable whisper.cpp debug mode
        debug_mode=False,                    
        # override audio context length; 0 uses the model default
        audio_ctx=0,                         
        # enable tinydiarize speaker-turn detectiontdrz_enable=False,                   


 
        # prompt and language defaults       
        # text prompt prepended before decoding begins
        initial_prompt=None,                 
        # keep the initial prompt across decode windows
        carry_initial_prompt=False,          
        # explicit prompt tokens instead of prompt text
        prompt_tokens=None,                  
        # number of prompt tokens to use
        prompt_n_tokens=0,                   
        # language code; empty string leaves language unset or auto
        language="",                        
        # ask whisper.cpp to detect language during decodedetect_language=False,               


        # suppression and threshold defaults        
        # suppress blank-token outputs
        suppress_blank=True,                 
        # suppress non-speech tokens
        suppress_nst=False,                  
        # token timestamp probability threshold
        thold_pt=0.01,                       
        # summed token timestamp probability threshold
        thold_ptsum=0.01,                    
        # initial decoding temperature
        temperature=0.0,                     
        # maximum initial timestamp allowed
        max_initial_ts=1.0,                  
        # decoder length penalty value
        length_penalty=-1.0,                 
        # fallback temperature increment
        temperature_inc=0.2,                 
        # entropy threshold used to detect decoder failure
        entropy_thold=2.4,                   
        # log-probability threshold used to detect decoder failure
        logprob_thold=-1.0,                  
        # no-speech probability thresholdno_speech_thold=0.6,                 


        # grammar and VAD defaults        
        # starting grammar rule index when grammar rules are supplied
        i_start_rule=0,                      
        # penalty applied to tokens outside the active grammar
        grammar_penalty=100.0,               
        # enable VAD pre-segmentation inside pywhispercpp
        vad=False,                           
        # explicit VAD model path when VAD is enabledvad_model_path=None,                 
    )
