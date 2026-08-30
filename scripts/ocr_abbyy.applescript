on run arguments
    set inputFile to POSIX file (item 1 of arguments)
    set outputFile to POSIX file (item 2 of arguments)
    tell application "ABBYY FineReader for ScanSnap"
        recognize inputFile and export to outputFile as pdf silent mode true
        repeat while busy
            delay 1
        end repeat
    end tell
end run
