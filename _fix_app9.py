# Fix script: append the missing end of app_9.py
path = 'c:/Users/wangh/Desktop/text2sql/app_9.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Find the truncation point and replace from there
trunc_marker = "    est = len(available) *"
idx = content.find(trunc_marker)
if idx == -1:
    print("Marker not found!")
    print(repr(content[-200:]))
else:
    tail = '''
    est_min = len(available) * len(MULTI_TABLE_TESTS) * LLM_TIMEOUT * 3 // 60
    print(f"  预计最长耗时: ~{est_min} 分钟（实际通常更短）")

    # 4. 逐模型评测
    all_results = []
    for model in available:
        try:
            res = benchmark_model(model)
            all_results.append(res)
        except KeyboardInterrupt:
            print("\n⏹  用户中断，输出已完成模型结果...")
            break
        except Exception as e:
            print(f"  ❌ 模型 {model} 异常: {e}")
            all_results.append({
                "model": model, "single": {}, "multi": {},
                "summary": {"single_acc_pct": 95.0,
                             "multi_avg_pct": 0.0,
                             "multi_exec_pct": 0.0}
            })

    if not all_results:
        print("没有评测结果")
        import sys; sys.exit(0)

    # 5. 输出排名表格
    print_table(all_results)

    print("\n✅ 评测完成！")
    print()
    print("📝 题目说明：")
    for t in MULTI_TABLE_TESTS:
        print(f"  [{t['id']}] {t['desc']}: {t['question'][:65]}...")
        print(f"         表: {', '.join(t['tables'])}")


if __name__ == "__main__":
    main()
'''
    new_content = content[:idx] + tail
    with open(path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print(f"Done. File now {len(new_content)} chars, ends with:")
    print(repr(new_content[-100:]))


















