#pragma once
#include <charconv>
#include <cmath>
#include <istream>
#include <locale>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace busmap::detail {
template<class T> T integer(const std::string& text) {
    T value{};
    auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
    if (error != std::errc{} || end != text.data() + text.size())
        throw std::runtime_error("Invalid integer: " + text);
    return value;
}

inline double number(const std::string& text) {
    std::istringstream input(text);
    input.imbue(std::locale::classic());
    double value;
    if (!(input >> value) || !input.eof() || !std::isfinite(value))
        throw std::runtime_error("Invalid finite number: " + text);
    return value;
}

inline bool valid_hash(std::string_view text) {
    if (text.size() != 64) return false;
    for (char c : text) if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
    return true;
}

class TextReader {
public:
    explicit TextReader(std::istream& input) : input_(input) {}
    std::vector<std::string> row(std::size_t fields) {
        std::string line;
        if (!std::getline(input_, line)) throw std::runtime_error("Missing record at line " + std::to_string(line_ + 1));
        ++line_;
        std::istringstream tokens(line);
        tokens.imbue(std::locale::classic());
        std::vector<std::string> result;
        for (std::string token; tokens >> token;) result.push_back(token);
        if (result.size() != fields) throw std::runtime_error("Wrong field count at line " + std::to_string(line_));
        return result;
    }
    std::string field(std::string_view key) {
        auto values = row(2);
        if (values[0] != key) throw std::runtime_error("Expected header " + std::string(key));
        return values[1];
    }
    void expect(std::string_view key, std::string_view value) {
        if (field(key) != value) throw std::runtime_error("Unsupported " + std::string(key));
    }
    void finish() {
        std::string extra;
        if (std::getline(input_, extra)) throw std::runtime_error("Unexpected trailing record");
        if (input_.bad()) throw std::runtime_error("Input read failed");
    }
private:
    std::istream& input_;
    std::size_t line_ = 0;
};
}
