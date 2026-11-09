#include<iostream>
#include<fstream>
#include <unordered_map>
#include <vector>
#include<set>
#include<stack>
#include<queue>

using namespace std;
const char PATH_CHAR='1';
const char WALL_CHAR='0';
const char ROOT_CHAR='A';
const char GOAL_CHAR='B';


vector<pair<int,int>> movements={
    {0,0},
    {-1,0},
    {0,-1},
    {1,0},
    {0,1}
};



class Node{
    public:
        Node* parent=nullptr;
        pair<int,int>value;
        vector<Node*> children;

        Node(pair<int,int> v){
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
        void print(){
            cout<<"vaule "<<value.first<<" "<<value.second<<endl;
        }

};

vector<vector<char>>* loadMatrix(string file_path);

inline int getNodeNumber(pair<int,int> cords, const int COLS);
inline pair<int,int> getNodeCords( int num, const int COLS);
pair<int,int> findRoot(vector<vector<char>>*matrix);
int getNumOfPathChars(vector<vector<char>>*matrix);
vector<pair<int,int>>* dfs(vector<vector<char>>*matrix);

unordered_map<int,Node*>* createTree(vector<vector<char>> *matrix);

int main() {

    auto matrix= loadMatrix("laberinto_1.in");
    auto tree= createTree(matrix);

    for(auto n: *tree){
        n.second->print();
    }

    
    




}


vector<vector<char>> *loadMatrix(string file_path){
    ifstream fin(file_path);
    int rows, cols;
    fin>>rows>>cols;
    auto m= new vector<vector<char> >(rows,vector<char>(cols));
    for(auto &i: *m){
        for(auto &j:i){
            fin>>j;
        }
    }
    return m;
}

inline int getNodeNumber(pair<int,int> cords, const int COLS){
    
    if(cords.first<0 || cords.second<0) return -1;
    return cords.first*COLS + cords.second;

}

inline pair<int,int> getNodeCords( int num, const int COLS){

    return {num/COLS,num%COLS};
}




pair<int,int> findRoot(vector<vector<char>>*matrix){

    const int ROWS=(*matrix).size();
    const int COLS=(*matrix)[0].size();

    pair<int,int> root={-1,-1};
    // so we can know where the root node is
    for(int i =0; i < ROWS;i++){
        for(int j =0;j<COLS;j++){   
            if((*matrix)[i][j]==ROOT_CHAR){
                root={i,j}; 
                break;
            }
        }
    }

    return root;
}




int getNumOfPathChars(vector<vector<char>>*matrix){
    int n=0;
    for(auto i: *matrix){
        for(auto j: i){
            if(j==PATH_CHAR)n++;
        }
    }
    return n;
}





unordered_map<int,Node*>* createTree(vector<vector<char>> *matrix){
    auto root = findRoot(matrix);
    const int COLS=(*matrix)[0].size();
    auto r=getNodeNumber(root,COLS);
    
    auto tree = new unordered_map<int,Node*>;
    tree->insert({r,new Node(root)});

    queue<pair<int,int>> bfs_q;
    bfs_q.push(root);
    set<pair<int,int>>visited;
    visited.insert(root);


    while(bfs_q.size()){
            visited.insert(bfs_q.front());
            tree->insert({getNodeNumber(bfs_q.front(),COLS),new Node(bfs_q.front())});       

            for(auto &n: movements){
                pair<int,int> child={bfs_q.front().first + n.first,bfs_q.front().second+n.second};
                if(visited.find(child)!=visited.end()) continue;
                if(
                    child.first <0||
                    child.second <0||
                    child.first >= (*matrix).size()||
                    child.second >= (*matrix)[0].size()
                        )   continue;
                bfs_q.push(child);
            }
            bfs_q.pop();
    }
    return tree;
}


