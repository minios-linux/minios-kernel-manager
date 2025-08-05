#!/bin/bash

# Update translation template from Python source files
find lib -name "*.py" -exec xgettext --from-code=UTF-8 --language=Python --keyword=_ -o po/messages.pot {} +

# Update individual language files
for po_file in po/*.po; do
    if [ -f "$po_file" ]; then
        echo "Updating $po_file"
        msgmerge --update "$po_file" po/messages.pot
    fi
done

echo "Translation files updated."