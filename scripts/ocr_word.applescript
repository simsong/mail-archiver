on run arguments
    set inputFile to POSIX file (item 1 of arguments)
    set outputPath to item 2 of arguments
    tell application "Microsoft Word"
        open inputFile read only true add to recent files false confirm conversions false
        set convertedDocument to active document
        save as convertedDocument file name outputPath file format format Unicode text add to recent files false
        close convertedDocument saving no
    end tell
end run
