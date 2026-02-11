# TODO: Standardize Field Names in main.py for Incremental CSV Maintenance

- [ ] Change "Submission status" to "Submission" in result dictionary initialization
- [ ] Change "reason" to "Reason" in result dictionary initialization
- [ ] Update all assignments to result["Submission"] and result["Reason"]
- [ ] Update all references to use "Submission" and "Reason" keys
- [ ] Simplify save_results method by removing key mapping logic
- [ ] Test the changes to ensure CSV is maintained incrementally
