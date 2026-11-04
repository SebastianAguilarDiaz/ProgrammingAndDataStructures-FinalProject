#include<iostream>
#include<fstream>
#include <vector>


using namespace std;
const char PATH_CHAR='1';
const char WALL_CHAR='0';
const char ROOT_CHAR='A';
const char GOAL_CHAR='B';

class Node{
    public:
        Node* parent=nullptr;
        int value;
        vector<Node*> children;

        Node(int v){
            value=v;
        }

        Node* addChildren(Node* c){
            this->children.push_back(c);
            c->parent=this;
            return c;
        }
        Node* addParent(Node* p){
            this->parent=p;
            p->children.push_back(this);
            return p;
        }

        vector<Node*>* ancesters(){
        
            auto p=this->parent;
            auto antrs= new vector<Node*>;
            while(p!=nullptr){
                antrs->push_back(p);
                p=p->parent;
            }
            return antrs;
        }


};

vector<vector<char>>* loadMatrix(string file_path);



int main() {

    loadMatrix("laberinto_1.in");



}


vector<vector<char>> *loadMatrix(string file_path){
    ifstream fin(file_path);
    int rows, cols;
    fin>>rows>>cols;
    return nullptr;
}

