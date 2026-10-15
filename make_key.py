import sys, tw_core as tw
print(tw.create_key(sys.argv[1] if len(sys.argv) > 1 else "", int(sys.argv[2]) if len(sys.argv) > 2 else tw.FREE_DAILY))
