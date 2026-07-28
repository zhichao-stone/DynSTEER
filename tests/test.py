def test(data: int, results: list, outputs: dict) -> tuple[str, dict[str, int]]:
    results.append(f"r{data}")
    outputs[data] = f"d{data}"
    return f"s{data}", {f"k{data}": data}


results: list[str] = []
outputs: dict[int, str] = {}

datas: list[int] = [1, 2, 3, 4, 5]
for data in datas:
    test(data, results, outputs)
    print("="*15)
    print(data)
    print(results)
    print(outputs)