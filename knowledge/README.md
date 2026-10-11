Put your knowledge files here, in the four sub-folders named in config.yaml (knowledge.folders):

    knowledge/communication_templates/   *.md / *.txt / *.csv
    knowledge/policy_docs/
    knowledge/product_docs/               (CSV files: one row = one searchable record)
    knowledge/faqs/

Replace this README with your own folder contents; `python build_index.py` indexes whatever is there.
Folder names, chunk size and top_k are all in config.yaml.
