import json

build='["angular", {"txt": "test", "M": 8, "engine": "MyISAM"}]'
#build='["angular", 100]'
algo_args = json.loads(build)
print("ALGO args:", algo_args)
